"""Lock and status logic for the detached upgrade runner.

The lock is what stops a retried or double-submitted /upgrade from starting a
second concurrent run, which would have two upgrades fighting over the same
containers and staging paths. A lock that wrongly reports itself held blocks
every future upgrade on the device, so the malformed cases matter as much as
the happy path.
"""
import os
import subprocess
import sys
import pytest
from unittest.mock import patch, MagicMock

import upgrade_runner


@pytest.fixture
def lock(tmp_path, monkeypatch):
    path = str(tmp_path / 'upgrade.lock')
    monkeypatch.setattr(upgrade_runner, 'LOCK_PATH', path)
    yield path


@pytest.fixture
def dead_pid():
    proc = subprocess.Popen([sys.executable, '-c', 'pass'])
    proc.wait()
    return proc.pid


@pytest.fixture
def live_pid():
    proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    yield proc.pid
    proc.kill()
    proc.wait()


class TestLockAcquisition:

    def test_acquires_when_absent(self, lock):
        assert upgrade_runner.acquire_lock('run-1') is True
        assert upgrade_runner.lock_holder() == os.getpid()

    def test_second_holder_is_refused(self, lock, live_pid):
        with open(lock, 'w') as handle:
            handle.write('%d run-live\n' % live_pid)
        assert upgrade_runner.acquire_lock('run-2') is False
        assert upgrade_runner.lock_holder() == live_pid

    def test_release_frees_it(self, lock):
        upgrade_runner.acquire_lock('run-1')
        upgrade_runner.release_lock()
        assert upgrade_runner.lock_holder() is None
        assert upgrade_runner.acquire_lock('run-2') is True

    def test_release_is_safe_when_absent(self, lock):
        upgrade_runner.release_lock()  # must not raise

    def test_stale_lock_from_dead_process_is_reclaimed(self, lock, dead_pid):
        with open(lock, 'w') as handle:
            handle.write('%d run-dead\n' % dead_pid)
        assert upgrade_runner.lock_holder() is None
        assert upgrade_runner.acquire_lock('run-new') is True

    # A lock file that can never be reclaimed bricks upgrades on the device,
    # so every malformed shape has to fall back to "not held".
    @pytest.mark.parametrize('content,label', [
        ('not-a-pid\n', 'garbage text'),
        ('', 'empty file'),
        ('   \n', 'whitespace only'),
        ('0 run\n', 'pid zero'),
        ('-1 run\n', 'negative pid - os.kill would target a process group'),
        ('99999999 run\n', 'pid that cannot exist'),
    ])
    def test_malformed_lock_never_blocks_forever(self, lock, content, label):
        with open(lock, 'w') as handle:
            handle.write(content)
        assert upgrade_runner.lock_holder() is None, label
        assert upgrade_runner.acquire_lock('run-new') is True, label


class TestLockInfo:

    def test_reports_run_id(self, lock):
        upgrade_runner.acquire_lock('run-xyz')
        pid, run_id = upgrade_runner.lock_info()
        assert pid == os.getpid()
        assert run_id == 'run-xyz'

    def test_missing_run_id_is_tolerated(self, lock):
        with open(lock, 'w') as handle:
            handle.write('%d\n' % os.getpid())
        pid, run_id = upgrade_runner.lock_info()
        assert pid == os.getpid()
        assert run_id is None


class TestStatus:

    def test_idle_when_no_lock_and_no_record(self, lock):
        with patch.object(upgrade_runner, '_latest_record', return_value=None):
            assert upgrade_runner.status() == {'state': 'idle'}

    def test_new_run_does_not_report_the_previous_result(self, lock):
        """A started run whose shell steps aren't recorded yet must read as
        running - otherwise a poller sees the last run's 'completed'."""
        previous = {'id': 'older-run', 'state': 'completed', 'cur_step': 5,
                    'cur_step_txt': 'updated vernemq broker'}
        upgrade_runner.acquire_lock('fresh-run')
        with patch.object(upgrade_runner, '_latest_record', return_value=previous):
            result = upgrade_runner.status()
        assert result['state'] == 'running'
        assert result['id'] == 'fresh-run'
        assert result['cur_step_txt'] == 'fetching update'

    def test_reports_progress_for_the_current_run(self, lock):
        current = {'id': 'this-run', 'state': 'running', 'cur_step': 3,
                   'upgrade_steps': 6, 'cur_step_txt': 'updating vision server'}
        upgrade_runner.acquire_lock('this-run')
        with patch.object(upgrade_runner, '_latest_record', return_value=current):
            result = upgrade_runner.status()
        assert result['state'] == 'running'
        assert result['cur_step'] == 3
        assert result['running'] is True
        assert result['pid'] == os.getpid()

    def test_running_record_with_no_process_reads_as_interrupted(self, lock):
        """A crashed upgrade leaves state=running forever; say so plainly."""
        orphan = {'id': 'dead-run', 'state': 'running', 'cur_step': 2}
        with patch.object(upgrade_runner, '_latest_record', return_value=orphan):
            result = upgrade_runner.status()
        assert result['state'] == 'interrupted'
        assert result['running'] is False

    def test_completed_record_is_passed_through(self, lock):
        done = {'id': 'r', 'state': 'completed', 'cur_step': 6, 'upgrade_steps': 6}
        with patch.object(upgrade_runner, '_latest_record', return_value=done):
            result = upgrade_runner.status()
        assert result['state'] == 'completed'
        assert result['running'] is False


class TestMain:

    def test_refuses_to_start_when_one_is_running(self, lock, live_pid):
        with open(lock, 'w') as handle:
            handle.write('%d run-live\n' % live_pid)
        with patch.object(upgrade_runner, 'run') as run:
            rc = upgrade_runner.main(['run-2'] + ['1.9.3'] * 7)
        assert rc == 3
        assert not run.called

    def test_releases_the_lock_when_the_run_finishes(self, lock):
        with patch.object(upgrade_runner, 'run', return_value=0):
            assert upgrade_runner.main(['run-1'] + ['1.9.3'] * 7) == 0
        assert upgrade_runner.lock_holder() is None

    def test_releases_the_lock_when_the_run_crashes(self, lock):
        with patch.object(upgrade_runner, 'run', side_effect=RuntimeError('boom')), \
             patch.object(upgrade_runner, '_mark_failed') as marked:
            with pytest.raises(RuntimeError):
                upgrade_runner.main(['run-1'] + ['1.9.3'] * 7)
        assert upgrade_runner.lock_holder() is None, 'crash left the lock held'
        assert marked.called

    def test_rejects_missing_arguments(self, lock):
        assert upgrade_runner.main([]) == 2


class TestMarkFailed:
    """A failure has to land on the record the operator is already polling."""

    def test_fails_the_in_progress_record(self):
        collection = MagicMock()
        collection.find.return_value.sort.return_value.limit.return_value = [
            {'_id': 'objid-1', 'id': 'run-1', 'state': 'running'}]
        with patch.object(upgrade_runner, '_records', return_value=collection):
            upgrade_runner._mark_failed('run-1', 'disk full')

        collection.update_one.assert_called_once()
        query, update = collection.update_one.call_args[0]
        assert query == {'_id': 'objid-1'}
        assert update['$set']['state'] == 'failed'
        assert update['$set']['cur_step_txt'] == 'disk full'
        assert update['$set']['end_time']

    def test_creates_a_record_when_the_shell_never_started(self):
        """A refresh failure happens before the shell writes any record."""
        collection = MagicMock()
        collection.find.return_value.sort.return_value.limit.return_value = []
        with patch.object(upgrade_runner, '_records', return_value=collection):
            upgrade_runner._mark_failed('run-2', 'could not fetch')

        query, update = collection.update_one.call_args[0]
        assert query == {'id': 'run-2'}
        assert update['$set']['state'] == 'failed'
        assert update['$set']['id'] == 'run-2'
        assert update['$set']['start_time']
        assert collection.update_one.call_args[1]['upsert'] is True

    def test_a_mongo_outage_does_not_raise(self):
        """Losing the record must not turn a handled failure into a crash."""
        with patch.object(upgrade_runner, '_records',
                          side_effect=RuntimeError('no mongo')):
            upgrade_runner._mark_failed('run-3', 'disk full')  # must not raise


class TestLogPath:

    def test_creates_the_directory_and_names_the_file(self, tmp_path, monkeypatch):
        target = tmp_path / 'logs'
        monkeypatch.setattr(upgrade_runner, 'LOG_DIR', str(target))
        path = upgrade_runner.log_path('run-abc')
        assert path == str(target / 'upgrade-run-abc.log')
        assert target.is_dir()

    def test_returns_none_when_the_directory_cannot_be_made(self, monkeypatch):
        monkeypatch.setattr(upgrade_runner, 'LOG_DIR', '/proc/nope/logs')
        assert upgrade_runner.log_path('run-abc') is None


class TestFlexRunErrorMapping:

    def test_known_codes_are_named(self):
        assert 'git' in upgrade_runner.flex_run_error(11)

    def test_unknown_code_degrades_gracefully(self):
        assert upgrade_runner.flex_run_error(77) == 'Update fetch failed (exit 77)'
