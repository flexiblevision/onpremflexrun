"""The FTP drop-directory watcher.

The previous version of this file tested a copy of the logic pasted into the
test body; ftp_worker.py itself was never imported and sat at 0%. It could not
be imported, because importing it dropped the jobs collection and then entered
an infinite loop. Both are now behind main().

The behaviours that matter: an image is enqueued exactly once, a file that is
not an image is deleted rather than queued, and a re-uploaded file is not
processed twice.
"""
import pytest
from unittest.mock import patch, MagicMock, call

import ftp_worker as fw


@pytest.fixture(autouse=True)
def clear_processed():
    fw.processed.clear()
    yield
    fw.processed.clear()


@pytest.fixture
def queue():
    with patch.object(fw, 'job_queue') as q:
        q.enqueue.return_value = MagicMock(id='job-1')
        yield q


@pytest.fixture
def jobs():
    with patch.object(fw, 'job_collection') as c:
        yield c


@pytest.fixture
def sh():
    """subprocess.call and os.system, which do all the file movement."""
    with patch('subprocess.call') as sub, patch('os.system') as system:
        yield {'call': sub, 'system': system}


@pytest.fixture
def frozen_time():
    with patch('ftp_worker.time.time_ns', return_value=1_700_000_000_000_000_000):
        yield 1_700_000_000_000  # the millisecond value the module derives


class TestResetQueue:
    @pytest.mark.unit
    def test_empties_the_queue_and_drops_the_records(self, queue, jobs):
        fw.reset_queue()
        queue.empty.assert_called_once()
        jobs.drop.assert_called_once()

    @pytest.mark.unit
    def test_import_does_not_reset_anything(self, queue, jobs):
        # This is the reason the module was previously untestable: dropping a
        # live collection as an import side effect.
        import importlib
        importlib.reload(fw)
        queue.empty.assert_not_called()
        jobs.drop.assert_not_called()


class TestInsertJobRef:
    @pytest.mark.unit
    def test_records_the_job_against_its_filename(self, jobs, frozen_time):
        fw.insert_job_ref('job-1', 'ftp_123.jpg')

        assert jobs.insert_one.call_args[0][0] == {
            '_id': 'job-1',
            'type': 'ftp_job_ftp_123.jpg',
            'start_time': str(frozen_time),
            'status': 'running',
        }

    @pytest.mark.unit
    def test_start_time_is_milliseconds(self, jobs, frozen_time):
        fw.insert_job_ref('job-1', 'x.jpg')
        assert jobs.insert_one.call_args[0][0]['start_time'] == '1700000000000'


class TestProcessFile:
    @pytest.mark.unit
    def test_an_image_is_renamed_and_enqueued(self, queue, jobs, sh, frozen_time):
        fw.process_file('/home/ftp', 'photo.jpg')

        assert sh['call'].call_args_list == [
            call(['mv', '/home/ftp/photo.jpg', '/home/ftp/photo.jpg']),
            call(['mv', '/home/ftp/photo.jpg', '/home/ftp/ftp_1700000000000.jpg']),
        ]
        queue.enqueue.assert_called_once()
        assert queue.enqueue.call_args[0][1] == 'ftp_1700000000000.jpg'

    @pytest.mark.unit
    def test_the_filename_is_lowercased_first(self, queue, jobs, sh, frozen_time):
        # Downstream parsing assumes a lowercase extension.
        fw.process_file('/home/ftp', 'PHOTO.JPG')
        assert sh['call'].call_args_list[0] == call(
            ['mv', '/home/ftp/PHOTO.JPG', '/home/ftp/photo.jpg'])

    @pytest.mark.unit
    @pytest.mark.parametrize('name', ['a.jpg', 'a.png', 'a.tif', 'a.bmp'])
    def test_every_supported_extension_is_enqueued(self, name, queue, jobs, sh, frozen_time):
        fw.process_file('/home/ftp', name)
        queue.enqueue.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.parametrize('name', ['notes.txt', 'archive.zip', 'a.jpeg', 'noext'])
    def test_anything_else_is_deleted(self, name, queue, jobs, sh):
        fw.process_file('/home/ftp', name)

        queue.enqueue.assert_not_called()
        sh['system'].assert_called_once_with('rm /home/ftp/' + name)

    @pytest.mark.unit
    def test_an_already_renamed_file_is_not_renamed_again(self, queue, jobs, sh, frozen_time):
        fw.process_file('/home/ftp', 'ftp_123.jpg')

        # Only the lowercase move, no second rename.
        assert len(sh['call'].call_args_list) == 1
        assert queue.enqueue.call_args[0][1] == 'ftp_123.jpg'

    @pytest.mark.unit
    def test_a_file_already_in_flight_is_not_enqueued_twice(self, queue, jobs, sh, frozen_time):
        fw.processed['ftp_123.jpg'] = 'processing'
        fw.process_file('/home/ftp', 'ftp_123.jpg')
        queue.enqueue.assert_not_called()

    @pytest.mark.unit
    def test_the_enqueued_name_is_what_gets_tracked(self, queue, jobs, sh, frozen_time):
        fw.process_file('/home/ftp', 'photo.jpg')
        # The renamed file is what the worker will look for on disk.
        assert 'ftp_1700000000000.jpg' in fw.processed
        assert 'photo.jpg' not in fw.processed

    @pytest.mark.unit
    def test_jobs_never_expire_out_of_the_queue(self, queue, jobs, sh, frozen_time):
        # A dropped result would leave the mongo record orphaned forever.
        fw.process_file('/home/ftp', 'photo.jpg')
        assert queue.enqueue.call_args[1]['result_ttl'] == -1
        assert queue.enqueue.call_args[1]['job_timeout'] == 99999999

    @pytest.mark.unit
    def test_the_job_record_is_written_with_the_queue_id(self, queue, jobs, sh, frozen_time):
        queue.enqueue.return_value = MagicMock(id='rq-42')
        fw.process_file('/home/ftp', 'photo.jpg')
        assert jobs.insert_one.call_args[0][0]['_id'] == 'rq-42'

    @pytest.mark.unit
    def test_a_file_in_a_subdirectory_is_moved_into_the_watch_root(self, queue, jobs, sh, frozen_time):
        # The worker only reads from /home/ftp, so a nested upload has to be
        # relocated or the job would never find its image.
        fw.process_file('/home/ftp/batch1', 'photo.jpg')

        assert sh['call'].call_args_list[1] == call(
            ['mv', '/home/ftp/batch1/photo.jpg', '/home/ftp/ftp_1700000000000.jpg'])


class TestScanOnce:
    @pytest.mark.unit
    def test_a_missing_watch_directory_is_a_no_op(self):
        with patch('os.path.exists', return_value=False), \
             patch.object(fw, 'process_file') as process:
            fw.scan_once()
        process.assert_not_called()

    @pytest.mark.unit
    def test_files_at_the_top_level_are_processed(self):
        with patch('os.path.exists', return_value=True), \
             patch('os.listdir', return_value=['a.jpg', 'b.png']), \
             patch('os.path.isdir', return_value=False), \
             patch.object(fw, 'process_file') as process:
            fw.scan_once()

        assert process.call_args_list == [call('/home/ftp', 'a.jpg'),
                                          call('/home/ftp', 'b.png')]

    @pytest.mark.unit
    def test_files_one_level_down_are_processed(self):
        listings = {'/home/ftp': ['batch1'], '/home/ftp/batch1': ['a.jpg']}

        with patch('os.path.exists', return_value=True), \
             patch('os.listdir', side_effect=lambda p: listings[p]), \
             patch('os.path.isdir', side_effect=lambda p: p == '/home/ftp/batch1'), \
             patch.object(fw, 'process_file') as process:
            fw.scan_once()

        process.assert_called_once_with('/home/ftp/batch1', 'a.jpg')

    @pytest.mark.unit
    def test_an_empty_subdirectory_is_skipped(self):
        listings = {'/home/ftp': ['batch1'], '/home/ftp/batch1': []}

        with patch('os.path.exists', return_value=True), \
             patch('os.listdir', side_effect=lambda p: listings[p]), \
             patch('os.path.isdir', return_value=True), \
             patch.object(fw, 'process_file') as process:
            fw.scan_once()

        process.assert_not_called()

    @pytest.mark.unit
    def test_a_nested_subdirectory_is_removed(self):
        # Only one level of nesting is supported; deeper trees are discarded
        # rather than walked.
        listings = {'/home/ftp': ['batch1'], '/home/ftp/batch1': ['deeper']}
        dirs = {'/home/ftp/batch1', '/home/ftp/batch1/deeper'}

        with patch('os.path.exists', return_value=True), \
             patch('os.listdir', side_effect=lambda p: listings[p]), \
             patch('os.path.isdir', side_effect=lambda p: p in dirs), \
             patch('os.system') as system, \
             patch.object(fw, 'process_file') as process:
            fw.scan_once()

        system.assert_called_once_with('rm -rf /home/ftp/batch1/deeper')
        process.assert_not_called()

    @pytest.mark.unit
    def test_an_empty_watch_directory_clears_the_in_flight_set(self):
        # Without this a filename could never be re-uploaded for the life of
        # the process.
        fw.processed['ftp_1.jpg'] = 'processing'

        with patch('os.path.exists', return_value=True), \
             patch('os.listdir', return_value=[]):
            fw.scan_once()

        assert fw.processed == {}

    @pytest.mark.unit
    def test_a_non_empty_directory_keeps_the_in_flight_set(self):
        fw.processed['ftp_1.jpg'] = 'processing'

        with patch('os.path.exists', return_value=True), \
             patch('os.listdir', return_value=['ftp_1.jpg']), \
             patch('os.path.isdir', return_value=False), \
             patch.object(fw, 'process_file'):
            fw.scan_once()

        assert 'ftp_1.jpg' in fw.processed


class TestMainLoop:
    @pytest.mark.unit
    def test_resets_the_queue_before_watching(self, main_thread_sleep):
        order = []
        ticks = []

        with patch.object(fw, 'reset_queue', side_effect=lambda: order.append('reset')), \
             patch('ftp_worker.time.sleep',
                   side_effect=lambda s: (order.append('sleep'),
                                          main_thread_sleep(ticks)(s))[1]), \
             patch.object(fw, 'scan_once'):
            with pytest.raises(KeyboardInterrupt):
                fw.main()

        assert order == ['reset', 'sleep']

    @pytest.mark.unit
    def test_scans_on_the_configured_interval(self, main_thread_sleep):
        ticks = []

        with patch.object(fw, 'reset_queue'), \
             patch('ftp_worker.time.sleep',
                   side_effect=main_thread_sleep(ticks, 3)), \
             patch.object(fw, 'scan_once') as scan:
            with pytest.raises(KeyboardInterrupt):
                fw.main()

        assert ticks == [fw.POLL_INTERVAL] * 3
        assert scan.call_count == 2
