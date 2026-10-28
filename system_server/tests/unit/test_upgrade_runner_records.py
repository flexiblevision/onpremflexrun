"""Upgrade record lookup and the remaining lock edge cases.

The lock is what stops two upgrades running at once. test_upgrade_runner.py
covers acquisition and staleness; these cover the paths around it - the mongo
handle, the corrupt-lock recovery, and the status record the /upgrade_status
endpoint serves.
"""
import errno
import os
import pytest
from unittest.mock import patch, MagicMock, call

import upgrade_runner as ur


@pytest.fixture
def lock_path(tmp_path, monkeypatch):
    path = str(tmp_path / 'upgrade.lock')
    monkeypatch.setattr(ur, 'LOCK_PATH', path)
    return path


class TestNowMs:
    @pytest.mark.unit
    def test_is_epoch_milliseconds(self):
        import datetime
        expected = datetime.datetime.now().timestamp() * 1000
        assert abs(ur._now_ms() - expected) < 5000


class TestRecords:
    @pytest.mark.unit
    def test_connects_to_the_upgrade_records_collection(self, monkeypatch):
        monkeypatch.delenv('MONGO_SERVER', raising=False)
        monkeypatch.delenv('MONGO_PORT', raising=False)
        client = MagicMock()

        with patch('pymongo.MongoClient', return_value=client) as factory:
            ur._records()

        assert factory.call_args[0][0] == '172.17.0.1'
        assert factory.call_args[0][1] == 27017
        client.__getitem__.assert_called_with('fvonprem')

    @pytest.mark.unit
    def test_the_host_can_be_overridden(self, monkeypatch):
        monkeypatch.setenv('MONGO_SERVER', '127.0.0.1')
        monkeypatch.setenv('MONGO_PORT', '27018')

        with patch('pymongo.MongoClient', return_value=MagicMock()) as factory:
            ur._records()

        assert factory.call_args[0][:2] == ('127.0.0.1', 27018)

    @pytest.mark.unit
    def test_the_connection_is_bounded(self):
        # This runs inside the /upgrade_status request; an unbounded server
        # selection would hang the endpoint on a device with no mongo.
        with patch('pymongo.MongoClient', return_value=MagicMock()) as factory:
            ur._records()

        assert factory.call_args[1]['serverSelectionTimeoutMS'] == 5000


class TestAcquireLockEdgeCases:
    @pytest.mark.unit
    def test_a_missing_lock_directory_is_created(self, tmp_path, monkeypatch):
        nested = tmp_path / 'run' / 'flexrun'
        monkeypatch.setattr(ur, 'LOCK_PATH', str(nested / 'upgrade.lock'))

        assert ur.acquire_lock('run-1') is True
        assert nested.is_dir()

    @pytest.mark.unit
    def test_an_uncreatable_lock_directory_propagates(self, monkeypatch):
        # makedirs failing is swallowed, but the open that follows then fails
        # with ENOENT rather than EEXIST and is re-raised. The upgrade does not
        # start and the caller gets a 500 - which is the right outcome, just
        # not via the False return the swallowed makedirs suggests.
        monkeypatch.setattr(ur, 'LOCK_PATH', '/proc/nope/upgrade.lock')

        with pytest.raises(OSError):
            ur.acquire_lock('run-1')

    @pytest.mark.unit
    def test_a_corrupt_lock_file_is_replaced(self, lock_path):
        # An unparseable lock must not block every future upgrade on the
        # device - there is no way to clear it remotely.
        with open(lock_path, 'w') as handle:
            handle.write('not a pid at all\n')

        assert ur.acquire_lock('run-1') is True

        with open(lock_path) as handle:
            assert handle.read().split()[1] == 'run-1'

    @pytest.mark.unit
    def test_an_empty_lock_file_is_replaced(self, lock_path):
        open(lock_path, 'w').close()

        assert ur.acquire_lock('run-1') is True

    @pytest.mark.unit
    def test_an_unexpected_open_error_propagates(self, lock_path):
        # Only EEXIST is a lock-contention signal; anything else is a real
        # fault and must not be mistaken for a stale lock.
        with patch('os.open', side_effect=OSError(errno.EACCES, 'denied')):
            with pytest.raises(OSError):
                ur.acquire_lock('run-1')

    @pytest.mark.unit
    def test_the_run_id_is_recorded_alongside_the_pid(self, lock_path):
        ur.acquire_lock('run-abc')

        with open(lock_path) as handle:
            pid, run_id = handle.read().split()

        assert int(pid) == os.getpid()
        assert run_id == 'run-abc'


class TestReleaseLock:
    @pytest.mark.unit
    def test_removes_the_lock_file(self, lock_path):
        ur.acquire_lock('run-1')
        ur.release_lock()

        assert not os.path.exists(lock_path)

    @pytest.mark.unit
    def test_releasing_an_unheld_lock_is_not_an_error(self, lock_path):
        ur.release_lock()


class TestLatestRecord:
    @pytest.mark.unit
    def test_returns_the_most_recent_upgrade(self):
        collection = MagicMock()
        collection.find.return_value.sort.return_value.limit.return_value = [
            {'_id': 'oid', 'run_id': 'run-9', 'status': 'complete'}]

        with patch.object(ur, '_records', return_value=collection):
            record = ur._latest_record()

        assert record == {'run_id': 'run-9', 'status': 'complete'}
        collection.find.return_value.sort.assert_called_once_with('start_time', -1)

    @pytest.mark.unit
    def test_the_mongo_id_is_stripped(self):
        # The record is serialised into an HTTP response.
        collection = MagicMock()
        collection.find.return_value.sort.return_value.limit.return_value = [
            {'_id': 'oid', 'run_id': 'run-9'}]

        with patch.object(ur, '_records', return_value=collection):
            assert '_id' not in ur._latest_record()

    @pytest.mark.unit
    def test_no_upgrades_yet_returns_none(self):
        collection = MagicMock()
        collection.find.return_value.sort.return_value.limit.return_value = []

        with patch.object(ur, '_records', return_value=collection):
            assert ur._latest_record() is None

    @pytest.mark.unit
    def test_an_unreachable_mongo_returns_none(self):
        # /upgrade_status must answer even when mongo is down mid-upgrade.
        with patch.object(ur, '_records', side_effect=Exception('no mongo')):
            assert ur._latest_record() is None
