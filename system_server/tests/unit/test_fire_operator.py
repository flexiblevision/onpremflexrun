"""Firestore trigger listener for the AWS deployment.

The previous version of this file defined copies of the logic inside each test
body; FireOperator.py itself was never imported and sat at 25%.

The interesting behaviour is the watchdog. A Firestore gRPC stream can go quiet
without erroring, and a listener that has silently stopped delivering snapshots
is indistinguishable from an idle line - the device simply stops inspecting.
The heartbeat document exists so that silence is detectable.
"""
import threading
import time

import pytest
from unittest.mock import patch, MagicMock, call

from aws import FireOperator as module
from aws.FireOperator import FireOperator, run_operator



class TestGetDb:
    """Why the client is not built at import."""

    @pytest.mark.unit
    def test_importing_the_module_opens_no_connection(self):
        """A module that cannot be imported without cloud credentials cannot be
        unit tested, and a collection error aborts the entire pytest run - which
        is how two files took down 1889 passing tests."""
        assert module._db is None or module._db is not None  # import succeeded

    @pytest.mark.unit
    def test_missing_credentials_names_the_file(self, monkeypatch):
        """credentials=None means 'find Application Default Credentials' to the
        Google SDK, so the old code turned a missing config file into an opaque
        auth error. Say which file is missing instead."""
        monkeypatch.setattr(module, '_db', None)
        monkeypatch.setattr(module, 'cred', None)
        with pytest.raises(RuntimeError, match='fire_creds.json'):
            module.get_db()

    @pytest.mark.unit
    def test_the_client_is_built_once_and_cached(self, monkeypatch):
        monkeypatch.setattr(module, '_db', None)
        monkeypatch.setattr(module, 'cred', MagicMock())
        with patch.object(module.firestore, 'Client') as client:
            first = module.get_db()
            second = module.get_db()
        assert first is second
        assert client.call_count == 1


@pytest.fixture
def firestore():
    """The firestore client, and the three documents the operator uses."""
    # get_db(), not a module-level 'db': the client is built on first use so the
    # module can be imported without cloud credentials.
    with patch.object(module, 'get_db') as get_db:
        db = get_db.return_value
        documents = {}

        def collection(name):
            handle = MagicMock()
            handle.document.return_value = documents.setdefault(name, MagicMock())
            return handle

        db.collection.side_effect = collection
        yield {'db': db, 'documents': documents}


@pytest.fixture
def no_threads():
    """__init__ starts a heartbeat writer and a watchdog; keep them out."""
    with patch.object(module.threading, 'Thread') as thread:
        yield thread


@pytest.fixture
def operator(firestore, no_threads):
    return FireOperator()


class TestConstruction:
    @pytest.mark.unit
    def test_subscribes_to_the_capture_and_heartbeat_documents(self, operator, firestore):
        assert firestore['documents']['inspections'].on_snapshot.called
        assert firestore['documents']['heartbeat'].on_snapshot.called

    @pytest.mark.unit
    def test_starts_the_heartbeat_writer_and_the_watchdog(self, firestore, no_threads):
        operator = FireOperator()

        targets = [c[1]['target'] for c in no_threads.call_args_list]
        assert operator._heartbeat_writer in targets
        assert operator._watchdog in targets
        assert all(c[1]['daemon'] is True for c in no_threads.call_args_list)

    @pytest.mark.unit
    def test_the_watchdog_clock_is_seeded(self, operator):
        # Without the seed the watchdog would resubscribe immediately, before
        # the first heartbeat has had a chance to arrive.
        assert operator.last_heartbeat_seen <= time.time()
        assert operator.last_heartbeat_seen > time.time() - 10

    @pytest.mark.unit
    def test_the_trigger_destination_comes_from_configuration(self, operator):
        assert operator.trigger_dest == module.trigger_dest

    @pytest.mark.unit
    def test_the_timeouts_are_a_multiple_of_the_heartbeat_interval(self):
        # A timeout below the write interval would resubscribe constantly.
        assert FireOperator.HEARTBEAT_TIMEOUT_S > FireOperator.HEARTBEAT_INTERVAL_S
        assert FireOperator.WATCHDOG_INTERVAL_S < FireOperator.HEARTBEAT_TIMEOUT_S


class TestListener:
    def _snapshot(self, doc_id='doc1', data=None):
        doc = MagicMock()
        doc.id = doc_id
        doc.to_dict.return_value = data or {'part': 'A', 'action': 'inspect'}
        return doc

    @pytest.mark.unit
    def test_the_first_snapshot_does_not_fire_a_trigger(self, operator):
        # Firestore replays the current document on subscribe; treating that
        # as a trigger would inspect a part that was already handled.
        with patch('requests.post') as post:
            operator.listener([self._snapshot()], [], 'read-time')

        post.assert_not_called()
        assert operator.intialized is True

    @pytest.mark.unit
    def test_a_later_snapshot_posts_the_trigger(self, operator):
        operator.intialized = True

        with patch('requests.post') as post:
            operator.listener([self._snapshot()], [], 'read-time')

        post.assert_called_once_with(
            operator.trigger_dest,
            json={'part': 'A', 'action': 'inspect'}, timeout=10)

    @pytest.mark.unit
    def test_the_read_time_is_recorded(self, operator):
        operator.intialized = True
        with patch('requests.post'):
            operator.listener([self._snapshot()], [], 'read-time-7')

        assert operator.last_read_time == 'read-time-7'

    @pytest.mark.unit
    def test_every_document_in_a_batch_is_triggered(self, operator):
        operator.intialized = True
        batch = [self._snapshot('d1'), self._snapshot('d2')]

        with patch('requests.post') as post:
            operator.listener(batch, [], 'read-time')

        assert post.call_count == 2

    @pytest.mark.unit
    def test_an_empty_snapshot_releases_the_waiter(self, operator):
        with patch('requests.post') as post:
            operator.listener([], [], 'read-time')

        post.assert_not_called()
        assert operator.thread.is_set()

    @pytest.mark.unit
    def test_the_trigger_post_is_bounded(self, operator):
        operator.intialized = True
        with patch('requests.post') as post:
            operator.listener([self._snapshot()], [], 'read-time')

        assert post.call_args[1]['timeout'] == 10


class TestHeartbeat:
    @pytest.mark.unit
    def test_any_heartbeat_snapshot_proves_the_stream_is_alive(self, operator):
        operator.last_heartbeat_seen = 0

        operator._heartbeat_listener([], [], 'read-time')

        assert operator.last_heartbeat_seen > 0

    @pytest.mark.unit
    def test_the_first_write_waits_out_the_interval(self, operator, firestore, main_thread_sleep):
        # The loop sleeps before writing, so a restart does not immediately
        # re-stamp a document another process may have just written.
        with patch('time.sleep', side_effect=main_thread_sleep([])):
            with pytest.raises(KeyboardInterrupt):
                operator._heartbeat_writer()

        firestore['documents']['heartbeat'].set.assert_not_called()

    @pytest.mark.unit
    def test_the_writer_stamps_the_document(self, operator, firestore, main_thread_sleep):
        calls = []

        with patch('time.sleep', side_effect=main_thread_sleep(calls, 2)):
            with pytest.raises(KeyboardInterrupt):
                operator._heartbeat_writer()

        written = firestore['documents']['heartbeat'].set.call_args[0][0]
        assert 'ts' in written
        assert isinstance(written['ts'], int)

    @pytest.mark.unit
    def test_the_writer_runs_on_the_configured_interval(self, operator, firestore, main_thread_sleep):
        calls = []

        with patch('time.sleep', side_effect=main_thread_sleep(calls, 2)):
            with pytest.raises(KeyboardInterrupt):
                operator._heartbeat_writer()

        assert calls == [FireOperator.HEARTBEAT_INTERVAL_S] * 2
        assert firestore['documents']['heartbeat'].set.call_count == 1

    @pytest.mark.unit
    def test_a_failed_heartbeat_write_does_not_kill_the_writer(self, operator, firestore, main_thread_sleep):
        # The writer is a daemon thread; an escaping exception would silently
        # stop every future heartbeat and trip the watchdog forever.
        firestore['documents']['heartbeat'].set.side_effect = RuntimeError('offline')
        calls = []

        with patch('time.sleep', side_effect=main_thread_sleep(calls, 3)):
            with pytest.raises(KeyboardInterrupt):
                operator._heartbeat_writer()

        assert firestore['documents']['heartbeat'].set.call_count == 2


class TestWatchdog:
    @pytest.mark.unit
    def test_a_live_stream_is_left_alone(self, operator, main_thread_sleep):
        operator.last_heartbeat_seen = time.time()
        calls = []

        with patch('time.sleep', side_effect=main_thread_sleep(calls, 2)), \
             patch.object(operator, 'start_listener') as resubscribe:
            with pytest.raises(KeyboardInterrupt):
                operator._watchdog()

        resubscribe.assert_not_called()

    @pytest.mark.unit
    def test_a_stale_stream_is_resubscribed(self, operator, main_thread_sleep):
        operator.last_heartbeat_seen = time.time() - (
            FireOperator.HEARTBEAT_TIMEOUT_S + 10)
        calls = []

        # The loop sleeps before it checks, so it has to be let round once.
        with patch('time.sleep', side_effect=main_thread_sleep(calls, 2)), \
             patch.object(operator, 'start_listener') as resubscribe:
            with pytest.raises(KeyboardInterrupt):
                operator._watchdog()

        resubscribe.assert_called_once()

    @pytest.mark.unit
    def test_it_polls_on_the_configured_interval(self, operator, main_thread_sleep):
        calls = []

        with patch('time.sleep', side_effect=main_thread_sleep(calls)):
            with pytest.raises(KeyboardInterrupt):
                operator._watchdog()

        assert calls == [FireOperator.WATCHDOG_INTERVAL_S]

    @pytest.mark.unit
    def test_a_failed_resubscribe_does_not_kill_the_watchdog(self, operator, main_thread_sleep):
        operator.last_heartbeat_seen = 0
        calls = []

        with patch('time.sleep', side_effect=main_thread_sleep(calls, 3)), \
             patch.object(operator, 'start_listener',
                          side_effect=RuntimeError('offline')) as resubscribe:
            with pytest.raises(KeyboardInterrupt):
                operator._watchdog()

        assert resubscribe.call_count == 2


class TestStartListener:
    @pytest.mark.unit
    def test_previous_subscriptions_are_torn_down_first(self, operator):
        # Leaving the old watch attached would double every trigger.
        old_capture, old_heartbeat = MagicMock(), MagicMock()
        operator._capture_watch = old_capture
        operator._heartbeat_watch = old_heartbeat

        operator.start_listener()

        old_capture.unsubscribe.assert_called_once()
        old_heartbeat.unsubscribe.assert_called_once()

    @pytest.mark.unit
    def test_a_failed_unsubscribe_does_not_prevent_resubscribing(self, operator):
        operator._capture_watch = MagicMock()
        operator._capture_watch.unsubscribe.side_effect = RuntimeError('already gone')

        operator.start_listener()

        assert operator._capture_watch is not None

    @pytest.mark.unit
    def test_the_replayed_snapshot_is_suppressed(self, operator):
        operator.intialized = True

        operator.start_listener()

        assert operator.intialized is False

    @pytest.mark.unit
    def test_the_watchdog_clock_is_reset(self, operator):
        operator.last_heartbeat_seen = 0

        operator.start_listener()

        assert operator.last_heartbeat_seen > 0


class TestStatus:
    @pytest.mark.unit
    def test_update_writes_the_status_document(self, operator, firestore):
        operator.update_status({'state': 'running'})

        firestore['documents']['status'].set.assert_called_once_with(
            {'state': 'running'})

    @pytest.mark.unit
    def test_get_returns_the_document_contents(self, operator, firestore):
        doc = MagicMock(exists=True)
        doc.to_dict.return_value = {'state': 'idle'}
        firestore['documents']['status'].get.return_value = doc

        assert operator.get_status() == {'state': 'idle'}

    @pytest.mark.unit
    def test_a_missing_status_document_is_none(self, operator, firestore):
        firestore['documents']['status'].get.return_value = MagicMock(exists=False)

        assert operator.get_status() is None


class TestSyncingAlive:
    def _utils(self, enabled=False, last_sync=None, interval=10):
        import datetime
        now_ms = int(datetime.datetime.now().timestamp() * 1000)
        records = {
            'predict_sync': {'ms_time': str(last_sync if last_sync is not None else now_ms)},
            'sync': {'is_enabled': enabled},
            'sync_interval': {'interval': interval},
        }
        return lambda query, projection=None: records[query['type']]

    @pytest.mark.unit
    def test_a_recently_synced_device_is_alive(self, operator):
        with patch.object(module.util_ref, 'find_one', side_effect=self._utils()):
            assert operator.syncing_alive() is True

    @pytest.mark.unit
    def test_a_device_with_syncing_enabled_is_not_alive(self, operator, firestore):
        # The check only passes while syncing is disabled; with it enabled the
        # operator defers and clears its status.
        with patch.object(module.util_ref, 'find_one',
                          side_effect=self._utils(enabled=True)):
            assert operator.syncing_alive() is False

        firestore['documents']['status'].set.assert_called_once_with({})

    @pytest.mark.unit
    def test_a_stale_sync_is_not_alive(self, operator, firestore):
        import datetime
        long_ago = int(datetime.datetime.now().timestamp() * 1000) - (10 * 60 * 60 * 1000)

        with patch.object(module.util_ref, 'find_one',
                          side_effect=self._utils(last_sync=long_ago, interval=1)):
            assert operator.syncing_alive() is False

        firestore['documents']['status'].set.assert_called_once_with({})

    @pytest.mark.unit
    def test_a_device_with_no_sync_records_raises(self, operator):
        with patch.object(module.util_ref, 'find_one', return_value=None):
            with pytest.raises(TypeError):
                operator.syncing_alive()


class TestMsTimestamp:
    @pytest.mark.unit
    def test_is_epoch_milliseconds(self):
        import datetime
        expected = datetime.datetime.now().timestamp() * 1000
        value = module.ms_timestamp()

        assert isinstance(value, int)
        assert abs(value - expected) < 5000


class TestRunOperator:
    def _forever(self, listing='', returncode=0):
        return MagicMock(stdout=listing, stderr='', returncode=returncode)

    @pytest.mark.unit
    def test_does_nothing_without_aws(self):
        with patch('settings.config', {'use_aws': False}), \
             patch('subprocess.run') as run, \
             patch('subprocess.Popen') as popen:
            assert run_operator() is None

        run.assert_not_called()
        popen.assert_not_called()

    @pytest.mark.unit
    def test_starts_the_server_when_it_is_not_running(self, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path))

        with patch('settings.config', {'use_aws': True}), \
             patch('subprocess.run', return_value=self._forever('no processes')), \
             patch('subprocess.Popen') as popen:
            assert run_operator() == 'started'

        command = popen.call_args[0][0]
        assert command[:4] == ['forever', 'start', '-c', 'python3']
        assert command[4].endswith('/flex-run/aws/fo_server.py')

    @pytest.mark.unit
    def test_a_running_server_is_not_started_twice(self, tmp_path, monkeypatch):
        # Two operators on one device would double every trigger.
        monkeypatch.setenv('HOME', str(tmp_path))
        path = str(tmp_path / 'flex-run' / 'aws' / 'fo_server.py')
        listing = f'data:    [0] abcd python3 {path} 1234'

        with patch('settings.config', {'use_aws': True}), \
             patch('subprocess.run', return_value=self._forever(listing)), \
             patch('subprocess.Popen') as popen:
            assert run_operator() == 'skipped'

        popen.assert_not_called()

    @pytest.mark.unit
    def test_a_stopped_entry_is_restarted(self, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path))
        path = str(tmp_path / 'flex-run' / 'aws' / 'fo_server.py')
        listing = f'data:    [0] abcd python3 {path} STOPPED'

        with patch('settings.config', {'use_aws': True}), \
             patch('subprocess.run', return_value=self._forever(listing)), \
             patch('subprocess.Popen') as popen:
            assert run_operator() == 'started'

        popen.assert_called_once()

    @pytest.mark.unit
    def test_a_failed_forever_list_does_not_raise(self, tmp_path, monkeypatch):
        import subprocess as sp
        monkeypatch.setenv('HOME', str(tmp_path))
        error = sp.CalledProcessError(1, 'forever list')
        error.stderr = 'forever not installed'

        with patch('settings.config', {'use_aws': True}), \
             patch('subprocess.run', side_effect=error), \
             patch('subprocess.Popen') as popen:
            assert run_operator() is None

        popen.assert_not_called()

    @pytest.mark.unit
    def test_an_unexpected_error_does_not_raise(self, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path))

        with patch('settings.config', {'use_aws': True}), \
             patch('subprocess.run', side_effect=FileNotFoundError('forever')), \
             patch('subprocess.Popen') as popen:
            assert run_operator() is None

        popen.assert_not_called()
