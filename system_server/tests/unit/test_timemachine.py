"""Time machine install, record cleanup and the zip-push upload path.

Three of these functions do not run at all in their current form - Retry and
ms_day are never imported or defined, and validate_account returns a bound
method instead of calling it. Each is pinned with a test that names the defect,
so the behaviour is recorded rather than assumed and a fix shows up as a
deliberate test change.
"""
import json
import os
import pytest
from testsupport import thread_aware_sleep_mock
from unittest.mock import patch, MagicMock, call, mock_open

from timemachine import installer, cleanup, zip_push


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


class TestLocalZipPushInstall:
    @pytest.mark.unit
    def test_retry_is_used_but_never_imported(self):
        # installer.py references Retry in the enqueue call and imports only
        # Queue and Worker from rq. The function raises NameError before it
        # ever queues the verification job, so a local time machine install
        # never gets verified.
        with patch('time.sleep', new=thread_aware_sleep_mock()), patch('os.system'), \
             patch.object(installer.job_queue, 'enqueue'), \
             patch.object(installer, 'insert_job'):
            with pytest.raises(NameError, match='Retry'):
                installer.local_zip_push_install('local')

    @pytest.mark.unit
    def test_the_install_script_still_runs_before_the_failure(self, home):
        with patch('time.sleep', new=thread_aware_sleep_mock()), patch('os.system') as system, \
             patch.object(installer.job_queue, 'enqueue'):
            with pytest.raises(NameError):
                installer.local_zip_push_install('zip_push')

        script = home + '/flex-run/system_server/timemachine/local_zip_push.sh'
        assert system.call_args_list == [call('chmod +x ' + script),
                                          call('sh ' + script + ' zip_push')]

    @pytest.mark.unit
    def test_it_waits_before_installing(self, home):
        # The job runs immediately after the HTTP response; the delay lets the
        # request finish before docker starts churning.
        with patch('time.sleep', new=thread_aware_sleep_mock()) as sleep, patch('os.system'), \
             patch.object(installer.job_queue, 'enqueue'):
            with pytest.raises(NameError):
                installer.local_zip_push_install('local')

        sleep.assert_called_once_with(5)


class TestVerifyLocalInstall:
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
    @pytest.mark.unit
    def test_the_retention_arithmetic_references_an_undefined_name(self):
        # `time_back = time_now - (ms_day*days)`; the local is named s_day.
        # The function raises NameError on its third line, so
        # DELETE /cleanup_timemachine has never removed a record.
        with patch.object(cleanup, 'get_archive_days', return_value=30), \
             patch.object(cleanup.tm_records_db, 'find') as find:
            with pytest.raises(NameError, match='ms_day'):
                cleanup.cleanup_timemachine_records()

        find.assert_not_called()

    @pytest.mark.unit
    def test_the_failure_branch_also_references_an_undefined_name(self):
        # Past the ms_day fix there is a second one: the except arm does
        # `failed.append(path)` and no `path` exists in that scope, so any
        # record whose file is already gone raises instead of being recorded
        # in the 'failed' list the function returns.
        records = [{'id': 'r1', 'filepath_webm': '/a.webm', 'filepath_mp4': '/a.mp4'}]
        with patch.object(cleanup, 'get_archive_days', return_value=30), \
             patch.object(cleanup.tm_records_db, 'find', return_value=records), \
             patch('os.remove', side_effect=OSError('already gone')), \
             patch.dict(cleanup.__dict__, {'ms_day': 86400}):
            with pytest.raises(NameError, match='path'):
                cleanup.cleanup_timemachine_records()

    @pytest.mark.unit
    def test_a_successful_pass_removes_both_encodings_and_the_record(self, monkeypatch):
        records = [{'id': 'r1', 'filepath_webm': '/a.webm', 'filepath_mp4': '/a.mp4'},
                   {'id': 'r2', 'filepath_webm': '/b.webm', 'filepath_mp4': '/b.mp4'}]

        with patch.object(cleanup, 'get_archive_days', return_value=30), \
             patch.object(cleanup.tm_records_db, 'find', return_value=records), \
             patch.object(cleanup.tm_records_db, 'delete_one') as delete, \
             patch('os.remove') as remove, \
             patch.dict(cleanup.__dict__, {'ms_day': 86400}):
            logs = cleanup.cleanup_timemachine_records()

        assert logs == {'num_records': 0, 'removed': 2, 'failed': []}
        assert remove.call_count == 4
        assert delete.call_args_list == [call({'id': 'r1'}), call({'id': 'r2'})]

    @pytest.mark.unit
    def test_num_records_is_never_incremented(self, monkeypatch):
        # num_to_remove is initialised to 0 and never touched, so the report
        # always claims zero records were eligible.
        records = [{'id': 'r1', 'filepath_webm': '/a.webm', 'filepath_mp4': '/a.mp4'}]
        with patch.object(cleanup, 'get_archive_days', return_value=30), \
             patch.object(cleanup.tm_records_db, 'find', return_value=records), \
             patch.object(cleanup.tm_records_db, 'delete_one'), \
             patch('os.remove'), \
             patch.dict(cleanup.__dict__, {'ms_day': 86400}):
            assert cleanup.cleanup_timemachine_records()['num_records'] == 0

    @pytest.mark.unit
    def test_files_are_resolved_under_the_visioncell_home(self, monkeypatch):
        monkeypatch.setenv('HOME', '/root')
        records = [{'id': 'r1', 'filepath_webm': '/x.webm', 'filepath_mp4': '/x.mp4'}]

        with patch.object(cleanup, 'get_archive_days', return_value=30), \
             patch.object(cleanup.tm_records_db, 'find', return_value=records), \
             patch.object(cleanup.tm_records_db, 'delete_one'), \
             patch('os.remove') as remove, \
             patch.dict(cleanup.__dict__, {'ms_day': 86400}):
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

        assert [len(b) for b in batches] == [5, 5, 2]

    @pytest.mark.unit
    def test_a_partial_batch_is_still_returned(self):
        with patch('builtins.open', mock_open(read_data=b'')):
            batches = zip_push.batch_and_process(self._events(3))

        assert [len(b) for b in batches] == [3]

    @pytest.mark.unit
    def test_no_events_still_yields_one_empty_batch(self):
        # push_event_records iterates the result, so an empty batch is a no-op
        # rather than an error.
        assert zip_push.batch_and_process([]) == [[]]

    @pytest.mark.unit
    def test_each_entry_is_a_multipart_file_tuple(self):
        with patch('builtins.open', mock_open(read_data=b'')) as opener:
            batch = zip_push.batch_and_process(self._events(1))[0]

        device_id, (name, handle, content_type) = batch[0]
        assert name == '0.zip'
        assert content_type == 'application/zip'
        opener.assert_called_once_with('/home/visioncell/z/0.zip', 'rb')

    @pytest.mark.unit
    def test_the_device_id_keys_each_upload(self):
        with patch.object(zip_push, 'DEV_ID', 'dev-42'), \
             patch('builtins.open', mock_open(read_data=b'')):
            batch = zip_push.batch_and_process(self._events(1))[0]

        assert batch[0][0] == 'dev-42'

    @pytest.mark.unit
    def test_an_unregistered_device_falls_back_to_the_event_id(self):
        with patch.object(zip_push, 'DEV_ID', None), \
             patch('builtins.open', mock_open(read_data=b'')):
            batch = zip_push.batch_and_process(self._events(1))[0]

        assert batch[0][0] == 'e0'


def _batch(n=1):
    return [(f'e{i}', (f'{i}.zip', MagicMock(name=f'/tmp/{i}.zip'), 'application/zip'))
            for i in range(n)]


class TestMarkAsProcessed:
    @pytest.mark.unit
    def test_flags_the_record_and_deletes_the_archive(self):
        batch = _batch(2)
        with patch.object(zip_push.tm_records_db, 'update_one') as update, \
             patch('os.remove') as remove:
            zip_push.mark_as_processed(batch)

        assert update.call_count == 2
        assert update.call_args[0][0] == {'id': 'e1'}
        assert update.call_args[0][1]['$set']['processed'] is True
        assert remove.call_count == 2

    @pytest.mark.unit
    def test_an_already_deleted_archive_does_not_stop_the_batch(self, capsys):
        batch = _batch(2)
        with patch.object(zip_push.tm_records_db, 'update_one') as update, \
             patch('os.remove', side_effect=OSError('gone')):
            zip_push.mark_as_processed(batch)

        # Both records are still marked processed - the upload succeeded, and
        # a stuck local file must not make the device re-push it forever.
        assert update.call_count == 2


class TestMarkAsDequeued:
    @pytest.mark.unit
    def test_clears_the_queued_flag_so_the_event_is_retried(self):
        with patch.object(zip_push.tm_records_db, 'update_one') as update:
            zip_push.mark_as_dequeued(_batch(2))

        assert update.call_count == 2
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
