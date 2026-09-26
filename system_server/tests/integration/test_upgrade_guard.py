"""A failed flex-run refresh must never be followed by the container upgrade.

upgrade_system.sh is one of the files the refresh replaces, so continuing after
a failed refresh runs the OLD scripts against the NEW container versions. This
guarantee used to live in the /upgrade request; it now lives in the detached
runner, so it is tested there.
"""
import pytest
from unittest.mock import patch, MagicMock

import upgrade_runner


def _sequence(refresh_rc, system_rc=0):
    """Stand-in for subprocess.run that records which scripts were invoked."""
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append({'cmd': cmd, 'env': kwargs.get('env')})
        result = MagicMock()
        result.stdout = ''
        result.stderr = ''
        if 'upgrade_flex_run.sh' in cmd[1]:
            result.returncode = refresh_rc
            result.stderr = ('' if refresh_rc == 0 else
                             '[upgrade_flex_run] ERROR: clone of branch master failed')
        else:
            result.returncode = system_rc
        return result

    return fake_run, calls


def _ran(calls, name):
    return any(name in ' '.join(str(part) for part in c['cmd']) for c in calls)


class TestRefreshFailureStopsTheUpgrade:

    @pytest.mark.parametrize('code,expected', [
        (10, 'Bad or missing fvconfig.json'),
        (11, 'Could not fetch the update from git'),
        (12, 'Fetched update was incomplete'),
        (13, 'Not enough disk space'),
        (14, 'Copying the update into place failed'),
        (99, 'Update fetch failed (exit 99)'),
    ])
    def test_container_upgrade_is_skipped_and_reason_recorded(self, code, expected):
        fake_run, calls = _sequence(refresh_rc=code)
        with patch('subprocess.run', side_effect=fake_run), \
             patch.object(upgrade_runner, '_mark_failed') as marked:
            rc = upgrade_runner.run('run-1', ['1.9.3'] * 7)

        assert rc == code
        assert _ran(calls, 'upgrade_flex_run.sh')
        assert not _ran(calls, 'upgrade_system.sh'), \
            'container upgrade ran after a failed refresh'
        assert marked.called
        assert expected in marked.call_args[0][1]


class TestSuccessfulRefreshProceeds:

    def test_container_upgrade_runs_and_shares_the_run_id(self):
        fake_run, calls = _sequence(refresh_rc=0, system_rc=0)
        with patch('subprocess.run', side_effect=fake_run), \
             patch.object(upgrade_runner, '_mark_failed') as marked:
            rc = upgrade_runner.run('run-abc', ['1.9.3'] * 7)

        assert rc == 0
        assert _ran(calls, 'upgrade_system.sh')
        assert not marked.called

        system = [c for c in calls if 'upgrade_system.sh' in c['cmd'][1]][0]
        # The shell records step progress against this id, so the API and the
        # records refer to the same upgrade.
        assert system['env']['FLEXRUN_RUN_ID'] == 'run-abc'
        # The seven version arguments must be passed through in order.
        assert system['cmd'][2:] == ['1.9.3'] * 7

    def test_container_upgrade_failure_is_recorded(self):
        fake_run, calls = _sequence(refresh_rc=0, system_rc=2)
        with patch('subprocess.run', side_effect=fake_run), \
             patch.object(upgrade_runner, '_mark_failed') as marked:
            rc = upgrade_runner.run('run-2', ['1.9.3'] * 7)

        assert rc == 2
        assert marked.called
        assert 'did not complete' in marked.call_args[0][1]
