"""The background workers: assembly sync, device sync, FTP prediction and the
prediction-server watchdog.

The property that matters across all of them is that a failed push does not
lose data. Assembly sync claims records by clearing a dirty flag before it
uploads, so every failure path has to put the flag back or the progress is
silently dropped.
"""
import importlib.util
import json
import os
import sys
import pytest
from testsupport import thread_aware_sleep_mock
from unittest.mock import patch, MagicMock, call

from worker_scripts import assembly_sync, process_ftp, sync_worker


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))



# --------------------------------------------------------------------------
# assembly_sync
# --------------------------------------------------------------------------

def _assembly(assembly_id='a1'):
    return {'id': assembly_id, 'station': 'line3', 'needs_sync': True}


@pytest.fixture
def assemblies():
    with patch.object(assembly_sync, 'assembly_collection') as collection:
        yield collection


class TestGetUnsyncedAssemblies:
    @pytest.mark.unit
    def test_claims_records_by_clearing_the_dirty_flag(self, assemblies):
        # The claim has to be atomic: two concurrent sync cycles picking up the
        # same record would upload it twice.
        assemblies.find_one_and_update.side_effect = [_assembly('a1'), None]

        result = assembly_sync.get_unsynced_assemblies()

        assert [a['id'] for a in result] == ['a1']
        query, update = assemblies.find_one_and_update.call_args[0]
        assert query == {'needs_sync': True}
        assert update == {'$set': {'needs_sync': False}}

    @pytest.mark.unit
    def test_the_oldest_change_is_claimed_first(self, assemblies):
        assemblies.find_one_and_update.return_value = None
        assembly_sync.get_unsynced_assemblies()

        assert assemblies.find_one_and_update.call_args[1]['sort'] == \
            [('modified_at', 1)]

    @pytest.mark.unit
    def test_stops_as_soon_as_nothing_is_left(self, assemblies):
        assemblies.find_one_and_update.side_effect = [_assembly('a1'), None]

        assembly_sync.get_unsynced_assemblies(limit=50)

        assert assemblies.find_one_and_update.call_count == 2

    @pytest.mark.unit
    def test_the_batch_size_is_respected(self, assemblies):
        assemblies.find_one_and_update.side_effect = \
            [_assembly(f'a{i}') for i in range(10)]

        result = assembly_sync.get_unsynced_assemblies(limit=3)

        assert len(result) == 3

    @pytest.mark.unit
    def test_the_mongo_id_is_projected_away(self, assemblies):
        assemblies.find_one_and_update.return_value = None
        assembly_sync.get_unsynced_assemblies()

        assert assemblies.find_one_and_update.call_args[1]['projection'] == {'_id': 0}

    @pytest.mark.unit
    def test_no_collection_returns_an_empty_batch(self):
        with patch.object(assembly_sync, 'assembly_collection', None):
            assert assembly_sync.get_unsynced_assemblies() == []

    @pytest.mark.unit
    def test_a_mongo_failure_returns_an_empty_batch(self, assemblies):
        assemblies.find_one_and_update.side_effect = Exception('no mongo')
        assert assembly_sync.get_unsynced_assemblies() == []


class TestMarkAssembliesSynced:
    @pytest.mark.unit
    def test_records_when_the_sync_happened(self, assemblies):
        assembly_sync.mark_assemblies_synced(['a1', 'a2'])

        query, update = assemblies.update_many.call_args[0]
        assert query == {'id': {'$in': ['a1', 'a2']}}
        assert 'last_synced_at' in update['$set']

    @pytest.mark.unit
    def test_an_empty_list_is_a_no_op(self, assemblies):
        assembly_sync.mark_assemblies_synced([])
        assemblies.update_many.assert_not_called()

    @pytest.mark.unit
    def test_no_collection_is_a_no_op(self):
        with patch.object(assembly_sync, 'assembly_collection', None):
            assembly_sync.mark_assemblies_synced(['a1'])

    @pytest.mark.unit
    def test_a_mongo_failure_is_swallowed(self, assemblies):
        # The upload already succeeded; failing here must not trigger a retry
        # that would duplicate it.
        assemblies.update_many.side_effect = Exception('no mongo')
        assembly_sync.mark_assemblies_synced(['a1'])


class TestMarkAssembliesNeedSync:
    @pytest.mark.unit
    def test_restores_the_dirty_flag(self, assemblies):
        assembly_sync.mark_assemblies_need_sync(['a1'])

        query, update = assemblies.update_many.call_args[0]
        assert query == {'id': {'$in': ['a1']}}
        assert update == {'$set': {'needs_sync': True}}

    @pytest.mark.unit
    def test_an_empty_list_is_a_no_op(self, assemblies):
        assembly_sync.mark_assemblies_need_sync([])
        assemblies.update_many.assert_not_called()

    @pytest.mark.unit
    def test_a_mongo_failure_is_swallowed(self, assemblies):
        assemblies.update_many.side_effect = Exception('no mongo')
        assembly_sync.mark_assemblies_need_sync(['a1'])


class TestSyncToCloud:
    def _response(self, status=200, body=None):
        response = MagicMock(status_code=status)
        response.json.return_value = body or {'synced_ids': ['a1'], 'failed_ids': []}
        return response

    @pytest.mark.unit
    def test_posts_the_batch_and_returns_the_verdicts(self):
        with patch('requests.post', return_value=self._response()) as post, \
             patch.object(assembly_sync, 'get_cloud_domain',
                          return_value='https://cloud.example'):
            synced, failed = assembly_sync.sync_to_cloud([_assembly()], 'tok')

        assert (synced, failed) == (['a1'], [])
        assert post.call_args[0][0] == \
            'https://cloud.example/api/assembly/progress/sync'
        assert post.call_args[1]['headers']['Authorization'] == 'Bearer tok'
        assert post.call_args[1]['json'] == {'assemblies': [_assembly()]}

    @pytest.mark.unit
    def test_partial_failures_are_reported(self):
        body = {'synced_ids': ['a1'], 'failed_ids': ['a2']}
        with patch('requests.post', return_value=self._response(body=body)), \
             patch.object(assembly_sync, 'get_cloud_domain', return_value='https://c'):
            assert assembly_sync.sync_to_cloud(
                [_assembly('a1'), _assembly('a2')], 'tok') == (['a1'], ['a2'])

    @pytest.mark.unit
    def test_an_empty_batch_skips_the_request(self):
        with patch('requests.post') as post:
            assert assembly_sync.sync_to_cloud([], 'tok') == ([], [])
        post.assert_not_called()

    @pytest.mark.unit
    def test_a_non_200_fails_the_whole_batch(self):
        with patch('requests.post', return_value=self._response(status=502)), \
             patch.object(assembly_sync, 'get_cloud_domain', return_value='https://c'):
            synced, failed = assembly_sync.sync_to_cloud(
                [_assembly('a1'), _assembly('a2')], 'tok')

        assert synced == []
        assert failed == ['a1', 'a2']

    @pytest.mark.unit
    def test_a_timeout_fails_the_whole_batch(self):
        import requests as requests_module
        with patch('requests.post',
                   side_effect=requests_module.exceptions.Timeout()), \
             patch.object(assembly_sync, 'get_cloud_domain', return_value='https://c'):
            assert assembly_sync.sync_to_cloud([_assembly('a1')], 'tok') == \
                ([], ['a1'])

    @pytest.mark.unit
    def test_an_unreachable_cloud_fails_the_whole_batch(self):
        with patch('requests.post', side_effect=ConnectionError('offline')), \
             patch.object(assembly_sync, 'get_cloud_domain', return_value='https://c'):
            assert assembly_sync.sync_to_cloud([_assembly('a1')], 'tok') == \
                ([], ['a1'])

    @pytest.mark.unit
    def test_the_upload_is_bounded(self):
        with patch('requests.post', return_value=self._response()) as post, \
             patch.object(assembly_sync, 'get_cloud_domain', return_value='https://c'):
            assembly_sync.sync_to_cloud([_assembly()], 'tok')

        assert post.call_args[1]['timeout'] == assembly_sync.SYNC_TIMEOUT


class TestAssemblySyncJob:
    @pytest.mark.unit
    def test_a_fully_synced_batch_reports_success(self):
        with patch.object(assembly_sync, 'sync_to_cloud', return_value=(['a1'], [])), \
             patch.object(assembly_sync, 'mark_assemblies_synced') as synced, \
             patch.object(assembly_sync, 'mark_assemblies_need_sync') as reflag:
            assert assembly_sync.assembly_sync_job([_assembly()], 'tok') is True

        synced.assert_called_once_with(['a1'])
        reflag.assert_not_called()

    @pytest.mark.unit
    def test_failed_records_are_reflagged_for_the_next_cycle(self):
        # This is the data-loss guard: the pickup already cleared needs_sync.
        with patch.object(assembly_sync, 'sync_to_cloud', return_value=([], ['a1'])), \
             patch.object(assembly_sync, 'mark_assemblies_need_sync') as reflag:
            assert assembly_sync.assembly_sync_job([_assembly()], 'tok') is False

        reflag.assert_called_once_with(['a1'])

    @pytest.mark.unit
    def test_a_partial_batch_reports_failure_so_it_is_retried(self):
        with patch.object(assembly_sync, 'sync_to_cloud',
                          return_value=(['a1'], ['a2'])), \
             patch.object(assembly_sync, 'mark_assemblies_synced'), \
             patch.object(assembly_sync, 'mark_assemblies_need_sync'):
            assert assembly_sync.assembly_sync_job(
                [_assembly('a1'), _assembly('a2')], 'tok') is False

    @pytest.mark.unit
    def test_an_empty_batch_succeeds_trivially(self):
        with patch.object(assembly_sync, 'sync_to_cloud') as sync:
            assert assembly_sync.assembly_sync_job([], 'tok') is True
        sync.assert_not_called()

    @pytest.mark.unit
    def test_an_unexpected_error_reflags_the_entire_batch(self):
        with patch.object(assembly_sync, 'sync_to_cloud',
                          side_effect=RuntimeError('boom')), \
             patch.object(assembly_sync, 'mark_assemblies_need_sync') as reflag:
            assert assembly_sync.assembly_sync_job(
                [_assembly('a1'), _assembly('a2')], 'tok') is False

        reflag.assert_called_once_with(['a1', 'a2'])

    @pytest.mark.unit
    def test_records_without_an_id_are_skipped_when_reflagging(self):
        with patch.object(assembly_sync, 'sync_to_cloud',
                          side_effect=RuntimeError('boom')), \
             patch.object(assembly_sync, 'mark_assemblies_need_sync') as reflag:
            assembly_sync.assembly_sync_job([{'station': 'line3'}], 'tok')

        reflag.assert_called_once_with([])


class TestPushAssemblyProgressToCloud:
    @pytest.mark.unit
    def test_reports_the_counts(self):
        with patch.object(assembly_sync, 'get_unsynced_assemblies',
                          return_value=[_assembly('a1'), _assembly('a2')]), \
             patch.object(assembly_sync, 'sync_to_cloud',
                          return_value=(['a1'], ['a2'])), \
             patch.object(assembly_sync, 'mark_assemblies_synced'), \
             patch.object(assembly_sync, 'mark_assemblies_need_sync'):
            result = assembly_sync.push_assembly_progress_to_cloud('tok')

        assert result['synced_count'] == 1
        assert result['failed_count'] == 1
        assert result['success'] is False
        assert 'Failed to sync 1' in result['error']

    @pytest.mark.unit
    def test_nothing_to_sync_is_a_clean_success(self):
        with patch.object(assembly_sync, 'get_unsynced_assemblies', return_value=[]), \
             patch.object(assembly_sync, 'sync_to_cloud') as sync:
            result = assembly_sync.push_assembly_progress_to_cloud('tok')

        assert result == {'success': True, 'synced_count': 0,
                          'failed_count': 0, 'error': None}
        sync.assert_not_called()

    @pytest.mark.unit
    def test_a_fully_synced_batch_is_a_success(self):
        with patch.object(assembly_sync, 'get_unsynced_assemblies',
                          return_value=[_assembly('a1')]), \
             patch.object(assembly_sync, 'sync_to_cloud', return_value=(['a1'], [])), \
             patch.object(assembly_sync, 'mark_assemblies_synced'), \
             patch.object(assembly_sync, 'mark_assemblies_need_sync') as reflag:
            result = assembly_sync.push_assembly_progress_to_cloud('tok')

        assert result['success'] is True
        reflag.assert_not_called()

    @pytest.mark.unit
    def test_an_error_after_pickup_reflags_the_batch(self):
        with patch.object(assembly_sync, 'get_unsynced_assemblies',
                          return_value=[_assembly('a1')]), \
             patch.object(assembly_sync, 'sync_to_cloud',
                          side_effect=RuntimeError('boom')), \
             patch.object(assembly_sync, 'mark_assemblies_need_sync') as reflag:
            result = assembly_sync.push_assembly_progress_to_cloud('tok')

        assert result['success'] is False
        assert result['error'] == 'boom'
        reflag.assert_called_once_with(['a1'])

    @pytest.mark.unit
    def test_a_failure_before_pickup_cannot_report_which_records_to_reflag(self):
        # `assemblies` is referenced in the except arm but only assigned by the
        # call that raised, so an error inside get_unsynced_assemblies raises
        # again from the handler. Nothing was claimed at that point, so no
        # progress is lost - but the caller sees UnboundLocalError, not the
        # original fault.
        with patch.object(assembly_sync, 'get_unsynced_assemblies',
                          side_effect=RuntimeError('mongo down')):
            with pytest.raises(NameError):
                assembly_sync.push_assembly_progress_to_cloud('tok')


class TestPushAssemblyProgress:
    @pytest.mark.unit
    def test_enqueues_the_batch(self):
        job = MagicMock(id='job-1')
        with patch.object(assembly_sync, 'get_unsynced_assemblies',
                          return_value=[_assembly('a1')]), \
             patch.object(assembly_sync.job_queue, 'enqueue', return_value=job) as enqueue, \
             patch('worker_scripts.job_manager.insert_job') as insert:
            assert assembly_sync.push_assembly_progress('tok') is True

        assert enqueue.call_args[0][0] is assembly_sync.assembly_sync_job
        assert enqueue.call_args[0][2] == 'tok'
        insert.assert_called_once_with('job-1', 'Syncing_1_assemblies_with_cloud')

    @pytest.mark.unit
    def test_nothing_to_sync_enqueues_nothing(self):
        with patch.object(assembly_sync, 'get_unsynced_assemblies', return_value=[]), \
             patch.object(assembly_sync.job_queue, 'enqueue') as enqueue:
            assert assembly_sync.push_assembly_progress('tok') is True
        enqueue.assert_not_called()

    @pytest.mark.unit
    def test_the_job_is_retried(self):
        with patch.object(assembly_sync, 'get_unsynced_assemblies',
                          return_value=[_assembly('a1')]), \
             patch.object(assembly_sync.job_queue, 'enqueue',
                          return_value=MagicMock(id='j')) as enqueue, \
             patch('worker_scripts.job_manager.insert_job'):
            assembly_sync.push_assembly_progress('tok')

        assert enqueue.call_args[1]['retry'].max == 5
        assert enqueue.call_args[1]['job_timeout'] == 300

    @pytest.mark.unit
    def test_an_enqueue_failure_is_reported_not_raised(self):
        with patch.object(assembly_sync, 'get_unsynced_assemblies',
                          return_value=[_assembly('a1')]), \
             patch.object(assembly_sync.job_queue, 'enqueue',
                          side_effect=Exception('redis down')):
            assert assembly_sync.push_assembly_progress('tok') is False


# --------------------------------------------------------------------------
# process_ftp
# --------------------------------------------------------------------------

PRESET = {'ioType': 'FTP', 'modelName': 'widgets', 'modelVersion': 3,
          'presetId': 'p1'}


@pytest.fixture
def ftp_collections():
    with patch.object(process_ftp, 'io_ref') as io_ref, \
         patch.object(process_ftp, 'ftp_ref') as ftp_ref, \
         patch.object(process_ftp, 'util_ref') as util_ref:
        io_ref.find_one.return_value = PRESET
        ftp_ref.find_one.return_value = {'type': 'settings'}
        util_ref.find_one.return_value = {'token': 'tok'}
        yield {'io': io_ref, 'ftp': ftp_ref, 'util': util_ref}


def _base64_proc(payload=b'ZmFrZQ=='):
    proc = MagicMock()
    proc.communicate.return_value = (payload, b'')
    return proc


class TestProcessImg:
    @pytest.mark.unit
    def test_a_configured_preset_processes_and_removes_the_image(self, ftp_collections):
        with patch('os.path.exists', return_value=True), \
             patch('subprocess.Popen', return_value=_base64_proc()), \
             patch('os.system') as system:
            assert process_ftp.process_img('a.jpg') == (True, 200)

        system.assert_called_once_with('rm -rf /home/ftp/a.jpg')

    @pytest.mark.unit
    def test_no_ftp_preset_configured_does_nothing(self, ftp_collections):
        ftp_collections['io'].find_one.return_value = None

        with patch('subprocess.Popen') as popen, patch('os.system') as system:
            assert process_ftp.process_img('a.jpg') is False

        popen.assert_not_called()
        system.assert_not_called()

    @pytest.mark.unit
    def test_a_missing_file_is_reported(self, ftp_collections):
        with patch('os.path.exists', return_value=False), \
             patch('os.system') as system:
            assert process_ftp.process_img('a.jpg') is False
        system.assert_not_called()

    @pytest.mark.unit
    def test_the_usb_export_runs_when_enabled(self, ftp_collections):
        ftp_collections['ftp'].find_one.return_value = {'usb': True}

        with patch('os.path.exists', return_value=True), \
             patch('subprocess.Popen', return_value=_base64_proc()), \
             patch('os.system'), \
             patch.object(process_ftp, 'add_file_to_usb') as usb, \
             patch.object(process_ftp, 'predict_img') as predict:
            process_ftp.process_img('a.jpg')

        usb.assert_called_once()
        predict.assert_not_called()

    @pytest.mark.unit
    def test_the_prediction_runs_when_enabled(self, ftp_collections):
        ftp_collections['ftp'].find_one.return_value = {'predict': True}

        with patch('os.path.exists', return_value=True), \
             patch('subprocess.Popen', return_value=_base64_proc()), \
             patch('os.system'), \
             patch.object(process_ftp, 'predict_img') as predict:
            process_ftp.process_img('a.jpg')

        predict.assert_called_once()
        assert predict.call_args[0][1] == 'a.jpg'
        assert predict.call_args[0][2] == PRESET

    @pytest.mark.unit
    def test_both_actions_can_run(self, ftp_collections):
        ftp_collections['ftp'].find_one.return_value = {'usb': True, 'predict': True}

        with patch('os.path.exists', return_value=True), \
             patch('subprocess.Popen', return_value=_base64_proc()), \
             patch('os.system'), \
             patch.object(process_ftp, 'add_file_to_usb') as usb, \
             patch.object(process_ftp, 'predict_img') as predict:
            process_ftp.process_img('a.jpg')

        usb.assert_called_once()
        predict.assert_called_once()

    @pytest.mark.unit
    def test_no_ftp_settings_still_removes_the_image(self, ftp_collections):
        # Otherwise the drop directory fills up with files nothing will process.
        ftp_collections['ftp'].find_one.return_value = None

        with patch('os.path.exists', return_value=True), \
             patch('subprocess.Popen', return_value=_base64_proc()), \
             patch('os.system') as system:
            assert process_ftp.process_img('a.jpg') == (True, 200)

        system.assert_called_once_with('rm -rf /home/ftp/a.jpg')


class TestAddFileToUsb:
    @pytest.mark.unit
    def test_posts_the_image_to_the_local_save_endpoint(self):
        with patch('requests.post') as post:
            process_ftp.add_file_to_usb('base64data')

        assert post.call_args[0][0] == 'http://172.17.0.1:5001/save_img'
        assert post.call_args[1]['json'] == {'img': 'base64data'}

    @pytest.mark.unit
    def test_a_failed_upload_is_reported_not_raised(self, capsys):
        with patch('requests.post', side_effect=ConnectionError('refused')):
            assert process_ftp.add_file_to_usb('base64data') is False
        assert 'Upload to USB failed' in capsys.readouterr().out


class TestPredictImg:
    @pytest.mark.unit
    def test_uploads_the_file_to_the_predict_endpoint(self, ftp_collections, tmp_path):
        image = tmp_path / 'a.jpg'
        image.write_bytes(b'data')

        with patch.object(process_ftp, 'directory', str(tmp_path)), \
             patch('time.sleep', new=thread_aware_sleep_mock()), \
             patch('requests.post',
                   return_value=MagicMock(status_code=200)) as post:
            process_ftp.predict_img('b64', 'a.jpg', PRESET)

        url = post.call_args[0][0]
        assert '/api/capture/predict/upload/widgets/3' in url
        assert 'workstation=ftp_service' in url
        assert 'preset_id=p1' in url
        assert post.call_args[1]['headers'] == {'Authorization': 'Bearer tok'}

    @pytest.mark.unit
    def test_a_missing_file_is_reported_not_raised(self, ftp_collections, tmp_path, capsys):
        with patch.object(process_ftp, 'directory', str(tmp_path)), \
             patch('time.sleep', new=thread_aware_sleep_mock()):
            assert process_ftp.predict_img('b64', 'absent.jpg', PRESET) is False

        assert 'Prediction failed' in capsys.readouterr().out

    @pytest.mark.unit
    def test_an_unreachable_backend_is_reported_not_raised(self, ftp_collections, tmp_path):
        image = tmp_path / 'a.jpg'
        image.write_bytes(b'data')

        with patch.object(process_ftp, 'directory', str(tmp_path)), \
             patch('time.sleep', new=thread_aware_sleep_mock()), \
             patch('requests.post', side_effect=ConnectionError('refused')):
            assert process_ftp.predict_img('b64', 'a.jpg', PRESET) is False


# --------------------------------------------------------------------------
# sync_worker
# --------------------------------------------------------------------------

def _jwt_with_exp(exp):
    import base64 as b64
    payload = b64.urlsafe_b64encode(
        json.dumps({'exp': exp}).encode()).decode().rstrip('=')
    return 'header.' + payload + '.signature'


class TestDecodeBase64:
    @pytest.mark.unit
    def test_padding_is_restored(self):
        # JWT segments arrive stripped of '='; b64decode rejects them as-is.
        assert json.loads(sync_worker.decode_base64(
            _jwt_with_exp(1700000000).split('.')[1])) == {'exp': 1700000000}

    @pytest.mark.unit
    def test_already_padded_input_is_unchanged(self):
        import base64 as b64
        encoded = b64.b64encode(b'{"a": 1}').decode()
        assert sync_worker.decode_base64(encoded) == '{"a": 1}'


class TestTokenIsValid:
    @pytest.mark.unit
    def test_a_future_expiry_is_valid(self):
        import datetime
        future = datetime.datetime.now().timestamp() + 3600
        assert sync_worker.token_is_valid(_jwt_with_exp(future)) is True

    @pytest.mark.unit
    def test_a_past_expiry_is_invalid(self):
        import datetime
        past = datetime.datetime.now().timestamp() - 3600
        assert sync_worker.token_is_valid(_jwt_with_exp(past)) is False


class TestGetRefreshToken:
    @pytest.mark.unit
    def test_reads_the_credentials_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path))
        proc = MagicMock()
        proc.communicate.return_value = (b'  refresh-abc \n', b'')

        with patch('subprocess.Popen', return_value=proc) as popen:
            assert sync_worker.get_refresh_token() == 'refresh-abc'

        assert popen.call_args[0][0][1].endswith(
            '/flex-run/system_server/creds.txt')

    @pytest.mark.unit
    def test_an_empty_file_returns_nothing(self):
        proc = MagicMock()
        proc.communicate.return_value = (b'\n', b'')
        with patch('subprocess.Popen', return_value=proc):
            assert sync_worker.get_refresh_token() is None


class TestRefreshTokens:
    @pytest.mark.unit
    def test_exchanges_the_refresh_token_for_a_pair(self):
        response = MagicMock()
        response.json.return_value = {'id_token': 'id', 'access_token': 'acc'}

        with patch.object(sync_worker, 'get_refresh_token', return_value='r'), \
             patch.object(sync_worker.s, 'post', return_value=response) as post:
            assert sync_worker.refresh_tokens() == \
                {'id_token': 'id', 'access_token': 'acc'}

        assert post.call_args[0][0].endswith('/api/capture/auth/refresh_token')
        assert post.call_args[1]['json'] == {'refresh_token': 'r'}

    @pytest.mark.unit
    def test_an_unauthorised_device_cannot_refresh(self):
        with patch.object(sync_worker, 'get_refresh_token', return_value=None), \
             patch.object(sync_worker.s, 'post') as post:
            assert sync_worker.refresh_tokens() is False
        post.assert_not_called()

    @pytest.mark.unit
    def test_a_partial_response_is_rejected(self):
        response = MagicMock()
        response.json.return_value = {'id_token': 'id'}

        with patch.object(sync_worker, 'get_refresh_token', return_value='r'), \
             patch.object(sync_worker.s, 'post', return_value=response):
            assert sync_worker.refresh_tokens() is False

    @pytest.mark.unit
    def test_an_unreachable_backend_is_reported_not_raised(self):
        with patch.object(sync_worker, 'get_refresh_token', return_value='r'), \
             patch.object(sync_worker.s, 'post', side_effect=ConnectionError('x')):
            assert sync_worker.refresh_tokens() is False


class TestGetAuthToken:
    @pytest.fixture
    def stored_tokens(self):
        with patch.object(sync_worker.util_ref, 'find_one') as find:
            find.side_effect = lambda query, projection=None: (
                {'token': 'id-token'} if query['type'] == 'id_token'
                else {'token': 'access-token'})
            yield find

    @pytest.mark.unit
    def test_valid_tokens_are_returned_as_is(self, stored_tokens):
        with patch.object(sync_worker, 'token_is_valid', return_value=True), \
             patch.object(sync_worker, 'refresh_tokens') as refresh:
            assert sync_worker.get_auth_token() == \
                {'access_token': 'access-token', 'id_token': 'id-token'}
        refresh.assert_not_called()

    @pytest.mark.unit
    def test_an_expired_token_triggers_a_refresh(self, stored_tokens):
        with patch.object(sync_worker, 'token_is_valid', return_value=False), \
             patch.object(sync_worker, 'refresh_tokens',
                          return_value={'id_token': 'new'}) as refresh:
            assert sync_worker.get_auth_token() == {'id_token': 'new'}
        refresh.assert_called_once()

    @pytest.mark.unit
    def test_an_unauthorised_device_raises(self):
        # No token documents at all: the lookup result is subscripted without
        # a guard, so the sync loop dies rather than skipping the cycle.
        with patch.object(sync_worker.util_ref, 'find_one', return_value=None):
            with pytest.raises(TypeError):
                sync_worker.get_auth_token()


class TestCanSync:
    @pytest.mark.unit
    def test_returns_the_backend_verdict(self):
        response = MagicMock()
        response.json.return_value = True
        with patch.object(sync_worker.s, 'get', return_value=response) as get:
            assert sync_worker.can_sync() is True
        assert get.call_args[0][0].endswith('/api/capture/system/can_sync')

    @pytest.mark.unit
    def test_an_unreachable_backend_is_a_no(self):
        with patch.object(sync_worker.s, 'get', side_effect=ConnectionError('x')), \
             patch('time.sleep', new=thread_aware_sleep_mock()):
            assert sync_worker.can_sync() is False


class TestCheckAndCleanup:
    @pytest.mark.unit
    def test_posts_with_the_id_token(self):
        with patch.object(sync_worker, 'get_auth_token',
                          return_value={'id_token': 'id', 'access_token': 'acc'}), \
             patch.object(sync_worker.s, 'post') as post:
            sync_worker.check_and_cleanup()

        assert post.call_args[0][0].endswith(
            '/api/capture/system/will_purge_analytics')
        assert post.call_args[1]['headers']['Authorization'] == 'Bearer id'

    @pytest.mark.unit
    def test_without_tokens_nothing_is_posted(self):
        with patch.object(sync_worker, 'get_auth_token', return_value=None), \
             patch.object(sync_worker.s, 'post') as post:
            sync_worker.check_and_cleanup()
        post.assert_not_called()

    @pytest.mark.unit
    def test_an_unreachable_backend_is_reported_not_raised(self):
        with patch.object(sync_worker, 'get_auth_token',
                          return_value={'id_token': 'id'}), \
             patch.object(sync_worker.s, 'post', side_effect=ConnectionError('x')):
            sync_worker.check_and_cleanup()


class TestSyncDevice:
    @pytest.mark.unit
    def test_syncs_and_then_cleans_up(self):
        with patch.object(sync_worker, 'get_auth_token',
                          return_value={'id_token': 'id', 'access_token': 'acc'}), \
             patch.object(sync_worker, 'can_sync', return_value=True), \
             patch.object(sync_worker.s, 'get') as get, \
             patch('time.sleep', new=thread_aware_sleep_mock()), \
             patch.object(sync_worker, 'check_and_cleanup') as cleanup:
            sync_worker.sync_device()

        assert get.call_args[0][0].endswith('/api/capture/system/sync_db')
        assert get.call_args[1]['headers']['Access-Token'] == 'acc'
        cleanup.assert_called_once()

    @pytest.mark.unit
    def test_a_backend_that_declines_is_not_synced(self):
        with patch.object(sync_worker, 'get_auth_token',
                          return_value={'id_token': 'id', 'access_token': 'acc'}), \
             patch.object(sync_worker, 'can_sync', return_value=False), \
             patch.object(sync_worker.s, 'get') as get:
            sync_worker.sync_device()
        get.assert_not_called()

    @pytest.mark.unit
    def test_without_tokens_nothing_is_synced(self):
        with patch.object(sync_worker, 'get_auth_token', return_value=None), \
             patch.object(sync_worker, 'can_sync', return_value=True), \
             patch.object(sync_worker.s, 'get') as get:
            sync_worker.sync_device()
        get.assert_not_called()

    @pytest.mark.unit
    def test_a_failed_sync_does_not_run_the_cleanup(self):
        # Purging analytics after a sync that did not land would drop records
        # that were never uploaded.
        with patch.object(sync_worker, 'get_auth_token',
                          return_value={'id_token': 'id', 'access_token': 'acc'}), \
             patch.object(sync_worker, 'can_sync', return_value=True), \
             patch.object(sync_worker.s, 'get', side_effect=ConnectionError('x')), \
             patch('time.sleep', new=thread_aware_sleep_mock()), \
             patch.object(sync_worker, 'check_and_cleanup') as cleanup:
            sync_worker.sync_device()

        cleanup.assert_not_called()


class TestSyncWorkerMain:
    @pytest.mark.unit
    def test_waits_for_the_backend_before_the_first_sync(self, main_thread_sleep):
        calls = []

        with patch('time.sleep', side_effect=main_thread_sleep(calls, 4)), \
             patch.object(sync_worker, 'sync_device'):
            with pytest.raises(KeyboardInterrupt):
                sync_worker.main()

        assert calls[0] == sync_worker.STARTUP_DELAY

    @pytest.mark.unit
    def test_syncs_on_each_pass(self, main_thread_sleep):
        calls = []

        with patch('time.sleep', side_effect=main_thread_sleep(calls, 6)), \
             patch.object(sync_worker, 'sync_device') as sync:
            with pytest.raises(KeyboardInterrupt):
                sync_worker.main()

        assert sync.call_count >= 1
        assert sync_worker.LOOP_DELAY in calls

    @pytest.mark.unit
    def test_aws_credentials_are_refreshed_when_enabled(self, main_thread_sleep):
        aws = MagicMock()
        calls = []

        with patch('time.sleep', side_effect=main_thread_sleep(calls, 4)), \
             patch.object(sync_worker, 'use_aws', True), \
             patch.object(sync_worker, 'aws_client', aws, create=True), \
             patch.object(sync_worker, 'get_auth_token') as token, \
             patch.object(sync_worker, 'sync_device'):
            with pytest.raises(KeyboardInterrupt):
                sync_worker.main()

        aws.validate_expiry.assert_called()
        token.assert_called()

    @pytest.mark.unit
    def test_importing_does_not_start_syncing(self):
        # The module used to sleep 120s and then loop forever at import.
        with patch.object(sync_worker, 'sync_device') as sync:
            importlib.util.find_spec('worker_scripts.sync_worker')
        sync.assert_not_called()


# --------------------------------------------------------------------------
# ping_prediction_server
# --------------------------------------------------------------------------

def _load_ping():
    path = os.path.join(REPO, 'worker_scripts', 'ping_prediction_server.py')
    spec = importlib.util.spec_from_file_location('_ping_under_test', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules['_ping_under_test'] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop('_ping_under_test', None)
    return module


ping = _load_ping()


class TestPingInsertJob:
    @pytest.mark.unit
    def test_records_a_loading_job(self):
        with patch.object(ping.job_collection, 'update_one') as update:
            ping.insert_job('JOB1234')

        query, document, upsert = update.call_args[0]
        assert query == {'job_id': 'JOB1234'}
        fields = document['$set']
        assert fields['_id'] == 'JOB1234'
        assert fields['status'] == 'running'
        assert fields['job_type'] == 'loading_prediction'
        assert upsert is True


class TestPingMain:
    @pytest.mark.unit
    def test_no_models_means_nothing_to_watch(self):
        with patch.object(ping.models_collection, 'find_one', return_value=None), \
             patch('requests.get') as get:
            ping.main()
        get.assert_not_called()

    @pytest.mark.unit
    def test_a_reachable_server_clears_the_loading_jobs(self, main_thread_sleep):
        model = {'type': 'widgets', 'versions': ['3']}
        sleeps = []

        with patch.object(ping.models_collection, 'find_one', return_value=model), \
             patch('requests.get', return_value=MagicMock(status_code=200)) as get, \
             patch.object(ping.job_collection, 'delete_many') as delete, \
             patch('time.sleep', side_effect=main_thread_sleep(sleeps)):
            with pytest.raises(KeyboardInterrupt):
                ping.main()

        assert get.call_args[0][0] == \
            'http://172.17.0.1:8501/v1/models/widgets/metadata'
        delete.assert_called_once_with({'job_type': 'loading_prediction'})

    @pytest.mark.unit
    def test_an_unreachable_server_records_a_loading_job(self, main_thread_sleep):
        model = {'type': 'widgets', 'versions': ['3']}

        with patch.object(ping.models_collection, 'find_one', return_value=model), \
             patch('requests.get', side_effect=ConnectionError('refused')), \
             patch.object(ping, 'insert_job') as insert, \
             patch('time.sleep', side_effect=main_thread_sleep([])):
            with pytest.raises(KeyboardInterrupt):
                ping.main()

        insert.assert_called_once()

    @pytest.mark.unit
    def test_the_poll_interval_is_ten_seconds(self, main_thread_sleep):
        model = {'type': 'widgets', 'versions': ['3']}
        sleeps = []

        with patch.object(ping.models_collection, 'find_one', return_value=model), \
             patch('requests.get', return_value=MagicMock(status_code=200)), \
             patch.object(ping.job_collection, 'delete_many'), \
             patch('time.sleep', side_effect=main_thread_sleep(sleeps)):
            with pytest.raises(KeyboardInterrupt):
                ping.main()

        assert sleeps == [10]
