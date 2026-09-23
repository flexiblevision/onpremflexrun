"""Time machine install, record cleanup and the zip-push upload path.

Two of these functions still do not run in their current form - ms_day is never
defined, and validate_account returns a bound method instead of calling it. Each
is pinned with a test that names the defect, so the behaviour is recorded rather
than assumed and a fix shows up as a deliberate test change.
"""
import datetime
import json
import os
import pytest
from testsupport import thread_aware_sleep_mock
from unittest.mock import patch, MagicMock, call, mock_open

from timemachine import installer, cleanup, zip_push
from timemachine import analytics as tm_analytics


# --------------------------------------------------------------------------
# installer
# --------------------------------------------------------------------------

@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    return str(tmp_path)


class TestCloudInstall:
    @pytest.mark.unit
    def test_makes_the_script_executable_then_runs_it(self, home):
        with patch('os.system') as system:
            installer.cloud_install()

        script = home + '/flex-run/system_server/timemachine/cloud.sh'
        assert system.call_args_list == [call('chmod +x ' + script),
                                          call('sh ' + script + ' ')]

    @pytest.mark.unit
    def test_returns_nothing(self, home):
        # The route treats a falsy result as a failed install, so /enable
        # with type=cloud always reports 500 even when the script succeeds.
        with patch('os.system'):
            assert installer.cloud_install() is None


class TestLocalZipPushScript:
    SCRIPT = os.path.join(os.path.dirname(installer.__file__), 'local_zip_push.sh')

    @pytest.mark.unit
    @pytest.mark.parametrize('image', ['fvonprem/x86-eventor:prod',
                                       'fvonprem/x86-rtspserver:prod'])
    def test_pulls_before_it_runs(self, image):
        # docker run never refreshes an existing local tag, so a redeploy
        # without the pull kept restarting the first image ever installed.
        lines = open(self.SCRIPT).read().splitlines()
        pull = next(i for i, l in enumerate(lines) if 'docker pull ' + image in l)
        run = next(i for i, l in enumerate(lines) if l.strip().startswith('-t ' + image))
        assert pull < run

    @pytest.mark.unit
    def test_a_failed_pull_does_not_stop_the_redeploy(self):
        for line in open(self.SCRIPT).read().splitlines():
            if 'docker pull ' in line:
                assert '||' in line


class TestLocalZipPushInstall:
    @pytest.mark.unit
    def test_it_queues_the_verification_job(self):
        # Retry was used in the enqueue call and imported from rq nowhere, so
        # this raised NameError on the line that queues the verification and a
        # local install was never verified. rq then retried the whole job five
        # times, tearing down and recreating the containers on each pass.
        with patch('time.sleep', new=thread_aware_sleep_mock()), patch('os.system'), \
             patch.object(installer.job_queue, 'enqueue',
                          return_value=MagicMock(id='job-1')) as enqueue, \
             patch.object(installer, 'insert_job') as insert:
            assert installer.local_zip_push_install('local') is True

        assert enqueue.call_args[0][0] is installer.verify_local_install
        assert enqueue.call_args[1]['retry'].max == 5
        insert.assert_called_once_with('job-1', 'verify timemachine install')

    @pytest.mark.unit
    def test_the_install_script_runs_before_the_verification_is_queued(self, home):
        with patch('time.sleep', new=thread_aware_sleep_mock()), patch('os.system') as system, \
             patch.object(installer.job_queue, 'enqueue',
                          return_value=MagicMock(id='job-1')), \
             patch.object(installer, 'insert_job'):
            installer.local_zip_push_install('zip_push')

        script = home + '/flex-run/system_server/timemachine/local_zip_push.sh'
        assert system.call_args_list == [call('chmod +x ' + script),
                                          call('sh ' + script + ' zip_push')]

    @pytest.mark.unit
    def test_it_waits_before_installing(self, home):
        # The job runs immediately after the HTTP response; the delay lets the
        # request finish before docker starts churning.
        with patch('time.sleep', new=thread_aware_sleep_mock()) as sleep, patch('os.system'), \
             patch.object(installer.job_queue, 'enqueue',
                          return_value=MagicMock(id='job-1')), \
             patch.object(installer, 'insert_job'):
            installer.local_zip_push_install('local')

        sleep.assert_called_once_with(5)


class TestVerifyLocalInstall:
    @pytest.fixture(autouse=True)
    def addon_state(self):
        with patch.object(installer, 'addon_state') as state:
            yield state

    @pytest.mark.unit
    def test_both_services_up_is_a_pass(self):
        with patch('requests.get', return_value=MagicMock(status_code=200)):
            assert installer.verify_local_install() is True

    @pytest.mark.unit
    def test_checks_the_eventor_and_the_rtsp_server(self):
        with patch('requests.get', return_value=MagicMock(status_code=200)) as get:
            installer.verify_local_install()

        urls = [c[0][0] for c in get.call_args_list]
        assert urls == ['http://172.17.0.1:1934/api/eventor/actions/server_status',
                        'http://localhost:9997/v1/paths/list']

    @pytest.mark.unit
    def test_a_service_returning_non_200_fails_the_check(self):
        responses = [MagicMock(status_code=200), MagicMock(status_code=503)]
        with patch('requests.get', side_effect=responses):
            assert installer.verify_local_install() is False

    @pytest.mark.unit
    def test_an_unreachable_service_fails_the_check(self):
        with patch('requests.get', side_effect=ConnectionError('refused')):
            assert installer.verify_local_install() is False

    @pytest.mark.unit
    def test_one_unreachable_service_is_enough_to_fail(self):
        def get(url, *a, **kw):
            if '9997' in url:
                raise ConnectionError('refused')
            return MagicMock(status_code=200)

        with patch('requests.get', side_effect=get):
            assert installer.verify_local_install() is False


class TestVerifyLocalInstallRecordsTheAddon:
    """Time machine is ui.manage 'custom', so addon_routes skips it and nothing
    else writes its addon record. device_identity.reported_domains() builds the
    domain list this device sends to the cloud from that collection, so without
    these writes a device records clips while the console lists time_machine
    under unavailable_domains."""

    @pytest.fixture(autouse=True)
    def addon_state(self):
        with patch.object(installer, 'addon_state') as state:
            yield state

    @pytest.mark.unit
    def test_a_pass_marks_the_addon_enabled(self, addon_state):
        with patch('requests.get', return_value=MagicMock(status_code=200)):
            installer.verify_local_install()

        addon_state.mark_enabled.assert_called_once_with('timemachine')
        addon_state.mark_failed.assert_not_called()

    @pytest.mark.unit
    def test_a_failure_records_why(self, addon_state):
        with patch('requests.get', return_value=MagicMock(status_code=503)):
            installer.verify_local_install()

        addon_state.mark_enabled.assert_not_called()
        assert addon_state.mark_failed.call_args[0][0] == 'timemachine'
        assert 'services down' in addon_state.mark_failed.call_args[0][1]

    @pytest.mark.unit
    def test_the_record_is_written_under_the_name_device_identity_reads(self, addon_state):
        # reported_domains() maps this exact key to the time_machine domain.
        from worker_scripts.device_identity import ADDON_DOMAINS

        with patch('requests.get', return_value=MagicMock(status_code=200)):
            installer.verify_local_install()

        assert addon_state.mark_enabled.call_args[0][0] in ADDON_DOMAINS

    @pytest.mark.unit
    def test_a_state_write_failure_does_not_change_the_verdict(self, addon_state):
        # The install came up; a mongo blip must not report it as down.
        addon_state.mark_enabled.side_effect = Exception('mongo is away')
        with patch('requests.get', return_value=MagicMock(status_code=200)):
            assert installer.verify_local_install() is True


class TestValidateAccount:
    @pytest.mark.unit
    def test_posts_the_service_name_with_the_caller_token(self):
        with patch('requests.post', return_value=MagicMock(status_code=200)) as post, \
             patch.object(installer, 'get_cloud_domain',
                          return_value='https://cloud.example'):
            installer.validate_account('time_machine', 'tok')

        assert post.call_args[0][0] == \
            'https://cloud.example/api/capture/auth/validate_service'
        assert post.call_args[1]['headers'] == {'Authorization': 'Bearer tok'}
        assert post.call_args[1]['json'] == {'service': 'time_machine'}

    @pytest.mark.unit
    def test_a_200_returns_the_json_method_rather_than_the_body(self):
        # `return res.json` - no call. The bound method is always truthy, so
        # every 200 is treated as entitled regardless of what the cloud said,
        # including an explicit denial.
        response = MagicMock(status_code=200)
        response.json.return_value = {'valid': False}

        with patch('requests.post', return_value=response), \
             patch.object(installer, 'get_cloud_domain', return_value='https://c'):
            result = installer.validate_account('time_machine', 'tok')

        assert result is response.json
        assert callable(result)
        assert bool(result) is True

    @pytest.mark.unit
    def test_a_non_200_falls_through_to_allowing_the_feature(self):
        # The `is_valid = True #TESTING ONLY` default is still in place, so a
        # rejection from the cloud grants the feature.
        with patch('requests.post', return_value=MagicMock(status_code=403)), \
             patch.object(installer, 'get_cloud_domain', return_value='https://c'):
            assert installer.validate_account('time_machine', 'tok') is True

    @pytest.mark.unit
    def test_an_unreachable_cloud_allows_the_feature(self):
        with patch('requests.post', side_effect=ConnectionError('offline')), \
             patch.object(installer, 'get_cloud_domain', return_value='https://c'):
            assert installer.validate_account('time_machine', 'tok') is True


class TestInstallerMain:
    @pytest.mark.unit
    def test_without_a_type_flag_it_explains_and_exits(self, capsys):
        with patch('sys.argv', ['installer.py']), \
             patch.object(installer, 'local_zip_push_install') as local, \
             patch.object(installer, 'cloud_install') as cloud:
            installer.main()

        local.assert_not_called()
        cloud.assert_not_called()
        assert 'Type of timemachine' in capsys.readouterr().out

    @pytest.mark.unit
    def test_the_default_type_is_local(self):
        # -t is required to get past the guard, but only -u/--Type is read, so
        # the parsed value is discarded and the default is what runs.
        with patch('sys.argv', ['installer.py', '-t', 'zip_push']), \
             patch.object(installer, 'local_zip_push_install') as local:
            installer.main()

        local.assert_called_once_with('local')

    @pytest.mark.unit
    def test_an_unparseable_argument_is_reported(self, capsys):
        with patch('sys.argv', ['installer.py', '-t', 'local', '-z']), \
             patch.object(installer, 'local_zip_push_install') as local:
            installer.main()

        local.assert_not_called()
        assert capsys.readouterr().out.strip()


# --------------------------------------------------------------------------
# cleanup
# --------------------------------------------------------------------------

class TestGetArchiveDays:
    @pytest.mark.unit
    def test_reads_the_retention_window(self):
        with patch.object(cleanup.tm_db, 'find_one', return_value={'archive_days': '30'}):
            assert cleanup.get_archive_days() == 30

    @pytest.mark.unit
    def test_a_numeric_value_is_accepted(self):
        with patch.object(cleanup.tm_db, 'find_one', return_value={'archive_days': 7}):
            assert cleanup.get_archive_days() == 7

    @pytest.mark.unit
    def test_no_configuration_document_raises(self):
        with patch.object(cleanup.tm_db, 'find_one', return_value=None):
            with pytest.raises(TypeError):
                cleanup.get_archive_days()


class TestCleanupTimemachineRecords:
    """Recordings older than the archive window, and nothing newer.

    This used to raise NameError on its third line - `ms_day` where the local
    is `s_day` - so DELETE /cleanup_timemachine had never removed a record on
    any device. Two more sat behind it: `failed.append(path)` with no `path`
    in scope, and a num_records that was reported but never counted.
    """

    @pytest.mark.unit
    def test_it_keeps_one_interval_of_history(self):
        # archive_days of 1 removes what is older than 24 hours ago, measured
        # from now - not from whenever the job last managed to run.
        now = int(datetime.datetime.now().timestamp())
        with patch.object(cleanup, 'get_archive_days', return_value=1), \
             patch.object(cleanup.tm_records_db, 'find', return_value=[]) as find:
            cleanup.cleanup_timemachine_records()

        cutoff = find.call_args[0][0]['record_start_time']['$lt']
        assert abs((now - cutoff) - 86400) <= 2

    @pytest.mark.unit
    def test_the_window_scales_with_the_configured_days(self):
        now = int(datetime.datetime.now().timestamp())
        with patch.object(cleanup, 'get_archive_days', return_value=30), \
             patch.object(cleanup.tm_records_db, 'find', return_value=[]) as find:
            cleanup.cleanup_timemachine_records()

        cutoff = find.call_args[0][0]['record_start_time']['$lt']
        assert abs((now - cutoff) - 30 * 86400) <= 2

    @pytest.mark.unit
    def test_the_cutoff_is_in_seconds_like_the_records(self):
        # record_start_time is epoch seconds. A millisecond cutoff would sit
        # far in the future of every record and delete the whole archive.
        with patch.object(cleanup, 'get_archive_days', return_value=1), \
             patch.object(cleanup.tm_records_db, 'find', return_value=[]) as find:
            cleanup.cleanup_timemachine_records()

        cutoff = find.call_args[0][0]['record_start_time']['$lt']
        assert 1e9 < cutoff < 1e10

    @pytest.mark.unit
    def test_a_successful_pass_removes_both_encodings_and_the_record(self):
        records = [{'id': 'r1', 'filepath_webm': '/a.webm', 'filepath_mp4': '/a.mp4'},
                   {'id': 'r2', 'filepath_webm': '/b.webm', 'filepath_mp4': '/b.mp4'}]

        with patch.object(cleanup, 'get_archive_days', return_value=30), \
             patch.object(cleanup.tm_records_db, 'find', return_value=records), \
             patch.object(cleanup.tm_records_db, 'delete_one') as delete, \
             patch('os.remove') as remove:
            logs = cleanup.cleanup_timemachine_records()

        assert logs == {'num_records': 2, 'removed': 2, 'failed': []}
        assert remove.call_count == 4
        assert delete.call_args_list == [call({'id': 'r1'}), call({'id': 'r2'})]

    @pytest.mark.unit
    def test_a_file_that_is_already_gone_still_takes_its_record(self):
        # Otherwise the record is found again on every pass and the archive
        # never drains.
        records = [{'id': 'r1', 'filepath_webm': '/a.webm', 'filepath_mp4': '/a.mp4'}]

        with patch.object(cleanup, 'get_archive_days', return_value=30), \
             patch.object(cleanup.tm_records_db, 'find', return_value=records), \
             patch.object(cleanup.tm_records_db, 'delete_one') as delete, \
             patch('os.remove', side_effect=FileNotFoundError('gone')):
            logs = cleanup.cleanup_timemachine_records()

        assert logs['removed'] == 1
        assert logs['failed'] == []
        delete.assert_called_once_with({'id': 'r1'})

    @pytest.mark.unit
    def test_a_real_failure_is_reported_and_the_record_kept(self):
        records = [{'id': 'r1', 'filepath_webm': '/a.webm', 'filepath_mp4': '/a.mp4'}]

        with patch.object(cleanup, 'get_archive_days', return_value=30), \
             patch.object(cleanup.tm_records_db, 'find', return_value=records), \
             patch.object(cleanup.tm_records_db, 'delete_one') as delete, \
             patch('os.remove', side_effect=OSError('permission denied')):
            logs = cleanup.cleanup_timemachine_records()

        assert logs['removed'] == 0
        assert len(logs['failed']) == 2
        assert 'permission denied' in logs['failed'][0]
        delete.assert_not_called()

    @pytest.mark.unit
    def test_one_bad_record_does_not_stop_the_rest(self):
        records = [{'id': 'bad', 'filepath_webm': '/bad.webm', 'filepath_mp4': '/bad.mp4'},
                   {'id': 'ok', 'filepath_webm': '/ok.webm', 'filepath_mp4': '/ok.mp4'}]

        def remove(path):
            if 'bad' in path:
                raise OSError('permission denied')

        with patch.object(cleanup, 'get_archive_days', return_value=30), \
             patch.object(cleanup.tm_records_db, 'find', return_value=records), \
             patch.object(cleanup.tm_records_db, 'delete_one') as delete, \
             patch('os.remove', side_effect=remove):
            logs = cleanup.cleanup_timemachine_records()

        assert logs == {'num_records': 2, 'removed': 1,
                        'failed': logs['failed']}
        delete.assert_called_once_with({'id': 'ok'})

    @pytest.mark.unit
    def test_it_reports_how_many_were_eligible(self):
        records = [{'id': 'r1', 'filepath_webm': '/a.webm', 'filepath_mp4': '/a.mp4'}]
        with patch.object(cleanup, 'get_archive_days', return_value=30), \
             patch.object(cleanup.tm_records_db, 'find', return_value=records), \
             patch.object(cleanup.tm_records_db, 'delete_one'), \
             patch('os.remove'):
            assert cleanup.cleanup_timemachine_records()['num_records'] == 1

    @pytest.mark.unit
    def test_files_are_resolved_under_the_visioncell_home(self, monkeypatch):
        monkeypatch.setenv('HOME', '/root')
        records = [{'id': 'r1', 'filepath_webm': '/x.webm', 'filepath_mp4': '/x.mp4'}]

        with patch.object(cleanup, 'get_archive_days', return_value=30), \
             patch.object(cleanup.tm_records_db, 'find', return_value=records), \
             patch.object(cleanup.tm_records_db, 'delete_one'), \
             patch('os.remove') as remove:
            cleanup.cleanup_timemachine_records()

        assert remove.call_args_list == [
            call('/root/../home/visioncell/x.webm'),
            call('/root/../home/visioncell/x.mp4')]


# --------------------------------------------------------------------------
# zip_push
# --------------------------------------------------------------------------

class TestGetUnprocessedEvents:
    @pytest.mark.unit
    def test_returns_the_pending_events_and_marks_them_queued(self):
        found = [{'_id': 'oid1', 'id': 'e1', 'zip_name': 'a.zip'},
                 {'_id': 'oid2', 'id': 'e2', 'zip_name': 'b.zip'}]

        with patch.object(zip_push.tm_records_db, 'find', return_value=iter(found)), \
             patch.object(zip_push.tm_records_db, 'update_one') as update:
            result = zip_push.get_unprocessed_events()

        assert result['count'] == 2
        assert [e['id'] for e in result['events']] == ['e1', 'e2']
        assert all(e['queued'] is True for e in result['events'])
        assert update.call_count == 2

    @pytest.mark.unit
    def test_the_mongo_id_is_stripped_from_the_payload(self):
        # It is not JSON-serialisable and the events go straight into a job.
        found = [{'_id': 'oid1', 'id': 'e1'}]
        with patch.object(zip_push.tm_records_db, 'find', return_value=iter(found)), \
             patch.object(zip_push.tm_records_db, 'update_one'):
            events = zip_push.get_unprocessed_events()['events']

        assert '_id' not in events[0]

    @pytest.mark.unit
    def test_only_unprocessed_unqueued_zip_push_records_are_selected(self):
        with patch.object(zip_push.tm_records_db, 'find', return_value=iter([])) as find:
            zip_push.get_unprocessed_events()

        query = find.call_args[0][0]
        assert query['processed'] is False
        assert query['storage_type'] == 'zip_push'
        assert {'queued': False} in query['$or']

    @pytest.mark.unit
    def test_no_pending_events_reports_zero(self):
        with patch.object(zip_push.tm_records_db, 'find', return_value=iter([])):
            assert zip_push.get_unprocessed_events() == {'count': 0, 'events': []}


class TestBatchAndProcess:
    def _events(self, n):
        return [{'id': f'e{i}', 'zip_name': f'{i}.zip', 'zip_path': f'/z/{i}.zip'}
                for i in range(n)]

    @pytest.mark.unit
    def test_batches_of_five(self):
        with patch('builtins.open', mock_open(read_data=b'')):
            batches = zip_push.batch_and_process(self._events(12))

        assert [len(files) for files, _ in batches] == [5, 5, 2]

    @pytest.mark.unit
    def test_a_partial_batch_is_still_returned(self):
        with patch('builtins.open', mock_open(read_data=b'')):
            batches = zip_push.batch_and_process(self._events(3))

        assert [len(files) for files, _ in batches] == [3]

    @pytest.mark.unit
    def test_no_events_still_yields_one_empty_batch(self):
        # push_event_records skips an empty batch, so this stays a no-op rather
        # than an error.
        assert zip_push.batch_and_process([]) == [([], [])]

    @pytest.mark.unit
    def test_each_entry_is_a_multipart_file_tuple(self):
        with patch('builtins.open', mock_open(read_data=b'')) as opener:
            files, _ = zip_push.batch_and_process(self._events(1))[0]

        device_id, (name, handle, content_type) = files[0]
        assert name == '0.zip'
        assert content_type == 'application/zip'
        opener.assert_called_once_with('/home/visioncell/z/0.zip', 'rb')

    @pytest.mark.unit
    def test_each_upload_travels_with_its_record(self):
        # The multipart field name is the device id, shared across the batch, so
        # the record is what says which row to mark.
        with patch.object(zip_push, 'DEV_ID', 'dev-42'), \
             patch('builtins.open', mock_open(read_data=b'')):
            files, events = zip_push.batch_and_process(self._events(3))[0]

        assert [e['id'] for e in events] == ['e0', 'e1', 'e2']
        assert [f[0] for f in files] == ['dev-42'] * 3

    @pytest.mark.unit
    def test_the_device_id_keys_each_upload(self):
        with patch.object(zip_push, 'DEV_ID', 'dev-42'), \
             patch('builtins.open', mock_open(read_data=b'')):
            files, _ = zip_push.batch_and_process(self._events(1))[0]

        assert files[0][0] == 'dev-42'

    @pytest.mark.unit
    def test_an_unregistered_device_falls_back_to_the_event_id(self):
        with patch.object(zip_push, 'DEV_ID', None), \
             patch('builtins.open', mock_open(read_data=b'')):
            files, _ = zip_push.batch_and_process(self._events(1))[0]

        assert files[0][0] == 'e0'


def _files(n=1):
    return [('dev-42', (f'{i}.zip', MagicMock(name=f'/tmp/{i}.zip'), 'application/zip'))
            for i in range(n)]


def _records(n=1):
    return [{'id': f'e{i}', 'zip_name': f'{i}.zip', 'zip_path': f'/z/{i}.zip',
             'record_start_time': 1789000000 + i} for i in range(n)]


def _batch(n=1):
    return (_files(n), _records(n))


class TestMarkAsProcessed:
    @pytest.mark.unit
    def test_flags_the_record_and_deletes_the_archive(self):
        files, events = _batch(2)
        with patch.object(zip_push.tm_records_db, 'update_one') as update, \
             patch.object(zip_push.analytics, 'record_event'), \
             patch('os.remove') as remove:
            zip_push.mark_as_processed(files, events)

        assert update.call_count == 2
        assert update.call_args[0][0] == {'id': 'e1'}
        assert update.call_args[0][1]['$set']['processed'] is True
        assert remove.call_count == 2

    @pytest.mark.unit
    def test_the_event_id_is_used_not_the_device_id(self):
        # The upload tuple carries the device id; marking by it matched no row.
        files, events = _batch(1)
        with patch.object(zip_push.tm_records_db, 'update_one') as update, \
             patch.object(zip_push.analytics, 'record_event'), \
             patch('os.remove'):
            zip_push.mark_as_processed(files, events)

        assert update.call_args[0][0] == {'id': 'e0'}

    @pytest.mark.unit
    def test_a_delivered_event_joins_the_analytics_spine(self):
        files, events = _batch(2)
        with patch.object(zip_push.tm_records_db, 'update_one'), \
             patch.object(zip_push.analytics, 'record_event') as record, \
             patch('os.remove'):
            zip_push.mark_as_processed(files, events)

        assert record.call_count == 2
        assert record.call_args[0][0]['id'] == 'e1'

    @pytest.mark.unit
    def test_an_already_deleted_archive_does_not_stop_the_batch(self, capsys):
        files, events = _batch(2)
        with patch.object(zip_push.tm_records_db, 'update_one') as update, \
             patch.object(zip_push.analytics, 'record_event'), \
             patch('os.remove', side_effect=OSError('gone')):
            zip_push.mark_as_processed(files, events)

        # Both records are still marked processed - the upload succeeded, and
        # a stuck local file must not make the device re-push it forever.
        assert update.call_count == 2


class TestAnalyticsProducer:
    """The record that puts a time machine event on the analytics spine."""

    def _event(self, **over):
        event = {'id': 'e1', 'record_start_time': 1789000000,
                 'zip_name': 'a.zip', 'zip_path': '/z/a.zip',
                 'filepath_mp4': '/b/a.mp4', 'serial_number': 'SN-1'}
        event.update(over)
        return event

    @pytest.mark.unit
    def test_start_time_becomes_iso_with_an_offset(self):
        # record_start_time is epoch SECONDS while predictions are ms. Resolving
        # it here is what stops the spine guessing (PRODUCERS.md rule 1).
        record = tm_analytics.build_record(self._event())

        assert record['event_ts'] == '2026-09-10T00:26:40+00:00'

    @pytest.mark.unit
    def test_the_recording_span_is_end_ts_never_duration(self):
        record = tm_analytics.build_record(
            self._event(record_end_time=1789000030))

        assert record['end_ts'] == '2026-09-10T00:27:10+00:00'
        assert 'duration_ms' not in record

    @pytest.mark.unit
    def test_it_is_tagged_for_the_time_machine_pack(self):
        record = tm_analytics.build_record(self._event())

        assert record['domain'] == 'time_machine'
        assert record['synced'] is False
        assert isinstance(record['modified'], int)

    @pytest.mark.unit
    def test_no_bucket_is_claimed(self):
        # The recording lands in the *device's* bucket, resolved cloud-side.
        record = tm_analytics.build_record(self._event())

        assert 'bucket' not in record

    @pytest.mark.unit
    def test_the_device_is_carried_for_place_resolution(self):
        record = tm_analytics.build_record(self._event(), device_id='dev-42')

        assert record['metadata']['device_id'] == 'dev-42'

    @pytest.mark.unit
    @pytest.mark.parametrize('missing', ['id', 'record_start_time'])
    def test_an_unusable_event_is_skipped_not_guessed(self, missing):
        event = self._event()
        del event[missing]

        assert tm_analytics.build_record(event) is None

    @pytest.mark.unit
    def test_recording_upserts_so_a_re_push_does_not_duplicate(self):
        with patch.object(tm_analytics.analytics_coll, 'update_one') as update:
            assert tm_analytics.record_event(self._event()) is True

        assert update.call_args[0][0] == {'id': 'e1'}
        assert update.call_args[1]['upsert'] is True

    @pytest.mark.unit
    def test_a_store_failure_never_breaks_the_push(self):
        with patch.object(tm_analytics.analytics_coll, 'update_one',
                          side_effect=RuntimeError('mongo down')):
            assert tm_analytics.record_event(self._event()) is False


class TestMarkAsDequeued:
    @pytest.mark.unit
    def test_clears_the_queued_flag_so_the_event_is_retried(self):
        with patch.object(zip_push.tm_records_db, 'update_one') as update:
            zip_push.mark_as_dequeued(_records(2))

        assert update.call_count == 2
        assert update.call_args[0][0] == {'id': 'e1'}
        assert update.call_args[0][1] == {'$set': {'queued': False}}


class TestPushEventRecords:
    @pytest.fixture
    def batches(self):
        with patch.object(zip_push, 'batch_and_process', return_value=[_batch(2)]) as b:
            yield b

    @pytest.mark.unit
    def test_a_successful_push_marks_the_batch_processed(self, batches):
        with patch('requests.post', return_value=MagicMock(status_code=200)), \
             patch.object(zip_push, 'mark_as_processed') as processed, \
             patch.object(zip_push, 'mark_as_dequeued') as dequeued, \
             patch.object(zip_push, 'get_cloud_functions_base', return_value='https://fn/'):
            assert zip_push.push_event_records('https://c', 'tok', {'events': []}) is True

        processed.assert_called_once()
        dequeued.assert_not_called()

    @pytest.mark.unit
    def test_the_token_and_endpoint_are_set(self, batches):
        with patch('requests.post', return_value=MagicMock(status_code=200)) as post, \
             patch.object(zip_push, 'mark_as_processed'), \
             patch.object(zip_push, 'get_cloud_functions_base', return_value='https://fn/'):
            zip_push.push_event_records('https://c', 'tok', {'events': []})

        assert post.call_args[0][0] == 'https://fn/TMEventIngest'
        assert post.call_args[1]['headers'] == {'Authorization': 'Bearer tok'}
        assert post.call_args[1]['timeout'] == 30

    @pytest.mark.unit
    @pytest.mark.parametrize('status', [299, 200, 201])
    def test_any_2xx_counts_as_delivered(self, batches, status):
        with patch('requests.post', return_value=MagicMock(status_code=status)), \
             patch.object(zip_push, 'mark_as_processed') as processed, \
             patch.object(zip_push, 'get_cloud_functions_base', return_value='https://fn/'):
            zip_push.push_event_records('https://c', 'tok', {'events': []})

        processed.assert_called_once()

    @pytest.mark.unit
    def test_a_rejected_push_is_requeued_not_dropped(self, batches):
        with patch('requests.post', return_value=MagicMock(status_code=500)), \
             patch.object(zip_push, 'mark_as_processed') as processed, \
             patch.object(zip_push, 'mark_as_dequeued') as dequeued, \
             patch.object(zip_push, 'get_cloud_functions_base', return_value='https://fn/'):
            zip_push.push_event_records('https://c', 'tok', {'events': []})

        processed.assert_not_called()
        dequeued.assert_called_once()

    @pytest.mark.unit
    def test_an_unreachable_cloud_requeues_the_batch(self, batches):
        with patch('requests.post', side_effect=ConnectionError('offline')), \
             patch.object(zip_push, 'mark_as_dequeued') as dequeued, \
             patch.object(zip_push, 'get_cloud_functions_base', return_value='https://fn/'):
            assert zip_push.push_event_records('https://c', 'tok', {'events': []}) is True

        dequeued.assert_called_once()

    @pytest.mark.unit
    def test_one_failed_batch_does_not_stop_the_others(self):
        responses = [MagicMock(status_code=500), MagicMock(status_code=200)]
        with patch.object(zip_push, 'batch_and_process',
                          return_value=[_batch(1), _batch(1)]), \
             patch('requests.post', side_effect=responses), \
             patch.object(zip_push, 'mark_as_processed') as processed, \
             patch.object(zip_push, 'mark_as_dequeued') as dequeued, \
             patch.object(zip_push, 'get_cloud_functions_base', return_value='https://fn/'):
            zip_push.push_event_records('https://c', 'tok', {'events': []})

        assert processed.call_count == 1
        assert dequeued.call_count == 1
