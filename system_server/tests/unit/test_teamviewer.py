"""TeamViewer daemon and GUI bring-up behind /start_teamviewer.

TeamViewer is how a device that has gone wrong is reached at all. The daemon
running without the GUI is the failure that matters: the endpoint looks
successful, the machine reports an ID, and it is still unreachable. That case
is asserted explicitly.
"""
import pytest
from unittest.mock import patch, MagicMock, call

from testsupport import thread_aware_sleep_mock

from routes import system_routes as sr


def _completed(returncode=0, stdout='', stderr=''):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def no_sleep():
    # routes.system_routes.time IS the global time module, so this patch is
    # global - a plain mock would let pymongo's monitor threads spin, and would
    # count their calls against this fixture's call_count assertions.
    with patch('routes.system_routes.time.sleep',
               new=thread_aware_sleep_mock()) as sleep:
        yield sleep


@pytest.fixture
def as_root():
    with patch('os.geteuid', return_value=0):
        yield


@pytest.fixture
def as_user():
    with patch('os.geteuid', return_value=1000):
        yield


class TestRunCmd:
    @pytest.mark.unit
    def test_returns_code_and_trimmed_streams(self):
        with patch('subprocess.run', return_value=_completed(0, ' out \n', ' err \n')):
            assert sr._run_cmd(['true']) == (0, 'out', 'err')

    @pytest.mark.unit
    def test_none_streams_become_empty_strings(self):
        with patch('subprocess.run', return_value=_completed(0, None, None)):
            assert sr._run_cmd(['true']) == (0, '', '')

    @pytest.mark.unit
    def test_exception_is_reported_as_minus_one(self):
        # A missing binary or a timeout must not propagate out of the endpoint.
        with patch('subprocess.run', side_effect=FileNotFoundError('no teamviewer')):
            code, out, err = sr._run_cmd(['teamviewer'])

        assert code == -1
        assert out == ''
        assert 'no teamviewer' in err

    @pytest.mark.unit
    def test_timeout_is_passed_through(self):
        with patch('subprocess.run', return_value=_completed()) as run:
            sr._run_cmd(['true'], timeout=7)
        assert run.call_args[1]['timeout'] == 7


class TestPriv:
    @pytest.mark.unit
    def test_root_runs_the_command_unchanged(self, as_root):
        assert sr._priv(['systemctl', 'start', 'x']) == ['systemctl', 'start', 'x']

    @pytest.mark.unit
    def test_non_root_gets_non_interactive_sudo(self, as_user):
        # -n matters: a sudo that prompts would hang the request until the
        # subprocess timeout rather than failing fast.
        assert sr._priv(['systemctl', 'start', 'x']) == \
            ['sudo', '-n', 'systemctl', 'start', 'x']


class TestTeamviewerRunning:
    @pytest.mark.unit
    def test_active_unit_is_enough(self):
        with patch.object(sr, '_run_cmd', return_value=(0, 'active', '')) as run:
            assert sr._teamviewer_running() is True
        # The systemctl answer short-circuits the slower teamviewer call.
        assert run.call_count == 1

    @pytest.mark.unit
    def test_falls_back_to_the_daemon_status_command(self):
        answers = [(3, 'inactive', ''), (0, 'TeamViewer Daemon is running', '')]
        with patch.object(sr, '_run_cmd', side_effect=answers):
            assert sr._teamviewer_running() is True

    @pytest.mark.unit
    def test_daemon_reporting_not_running_is_not_running(self):
        answers = [(3, 'inactive', ''), (0, 'TeamViewer Daemon is not running', '')]
        with patch.object(sr, '_run_cmd', side_effect=answers):
            assert sr._teamviewer_running() is False

    @pytest.mark.unit
    def test_failed_status_command_is_not_running(self):
        answers = [(3, 'inactive', ''), (1, '', 'command not found')]
        with patch.object(sr, '_run_cmd', side_effect=answers):
            assert sr._teamviewer_running() is False

    @pytest.mark.unit
    def test_not_running_check_is_case_insensitive(self):
        answers = [(3, 'inactive', ''), (0, 'Daemon is NOT RUNNING', '')]
        with patch.object(sr, '_run_cmd', side_effect=answers):
            assert sr._teamviewer_running() is False


class TestWaitForTeamviewer:
    @pytest.mark.unit
    def test_returns_immediately_when_already_running(self, no_sleep):
        with patch.object(sr, '_teamviewer_running', return_value=True):
            assert sr._wait_for_teamviewer(timeout=5) is True
        no_sleep.assert_not_called()

    @pytest.mark.unit
    def test_polls_until_the_daemon_comes_up(self, no_sleep):
        with patch.object(sr, '_teamviewer_running', side_effect=[False, False, True]):
            assert sr._wait_for_teamviewer(timeout=5) is True
        assert no_sleep.call_count == 2

    @pytest.mark.unit
    def test_gives_up_at_the_deadline(self, no_sleep):
        with patch.object(sr, '_teamviewer_running', return_value=False), \
             patch('routes.system_routes.time.time', side_effect=[0, 1, 99]):
            assert sr._wait_for_teamviewer(timeout=5) is False


class TestGraphicalSession:
    @pytest.mark.unit
    def test_finds_the_active_x11_session(self):
        answers = [
            (0, 'c2 1000 visioncell seat0', ''),
            (0, 'Name=visioncell\nUser=1000\nType=x11\nActive=yes', ''),
        ]
        with patch.object(sr, '_run_cmd', side_effect=answers):
            assert sr._graphical_session() == {
                'user': 'visioncell', 'uid': '1000', 'type': 'x11'}

    @pytest.mark.unit
    def test_finds_a_wayland_session(self):
        answers = [
            (0, 'c2 1000 visioncell seat0', ''),
            (0, 'Name=visioncell\nUser=1000\nType=wayland\nActive=yes', ''),
        ]
        with patch.object(sr, '_run_cmd', side_effect=answers):
            assert sr._graphical_session()['type'] == 'wayland'

    @pytest.mark.unit
    def test_skips_inactive_and_tty_sessions(self):
        answers = [
            (0, 'c1 0 root\nc2 1000 visioncell\nc3 1001 other', ''),
            (0, 'Name=root\nUser=0\nType=tty\nActive=yes', ''),
            (0, 'Name=visioncell\nUser=1000\nType=x11\nActive=no', ''),
            (0, 'Name=other\nUser=1001\nType=x11\nActive=yes', ''),
        ]
        with patch.object(sr, '_run_cmd', side_effect=answers):
            assert sr._graphical_session()['user'] == 'other'

    @pytest.mark.unit
    def test_no_graphical_session_returns_none(self):
        answers = [
            (0, 'c1 0 root', ''),
            (0, 'Name=root\nUser=0\nType=tty\nActive=yes', ''),
        ]
        with patch.object(sr, '_run_cmd', side_effect=answers):
            assert sr._graphical_session() is None

    @pytest.mark.unit
    def test_loginctl_failure_returns_none(self):
        with patch.object(sr, '_run_cmd', return_value=(1, '', 'no loginctl')):
            assert sr._graphical_session() is None

    @pytest.mark.unit
    def test_blank_lines_are_skipped(self):
        answers = [
            (0, '\n\nc2 1000 visioncell\n', ''),
            (0, 'Name=visioncell\nUser=1000\nType=x11\nActive=yes', ''),
        ]
        with patch.object(sr, '_run_cmd', side_effect=answers):
            assert sr._graphical_session()['user'] == 'visioncell'

    @pytest.mark.unit
    def test_malformed_property_lines_are_ignored(self):
        answers = [
            (0, 'c2 1000 visioncell', ''),
            (0, 'garbage\nName=visioncell\nUser=1000\nType=x11\nActive=yes', ''),
        ]
        with patch.object(sr, '_run_cmd', side_effect=answers):
            assert sr._graphical_session()['user'] == 'visioncell'


class TestTeamviewerGuiRunning:
    @pytest.mark.unit
    def test_pgrep_match_means_running(self):
        with patch.object(sr, '_run_cmd', return_value=(0, '1234', '')):
            assert sr._teamviewer_gui_running() is True

    @pytest.mark.unit
    def test_pgrep_miss_means_not_running(self):
        with patch.object(sr, '_run_cmd', return_value=(1, '', '')):
            assert sr._teamviewer_gui_running() is False

    @pytest.mark.unit
    def test_matches_the_process_name_exactly(self):
        with patch.object(sr, '_run_cmd', return_value=(0, '1', '')) as run:
            sr._teamviewer_gui_running()
        assert run.call_args[0][0] == ['pgrep', '-x', 'TeamViewer']


class TestWaitForTeamviewerGui:
    @pytest.mark.unit
    def test_returns_immediately_when_already_up(self, no_sleep):
        with patch.object(sr, '_teamviewer_gui_running', return_value=True):
            assert sr._wait_for_teamviewer_gui(timeout=5) is True

    @pytest.mark.unit
    def test_gives_up_at_the_deadline(self, no_sleep):
        with patch.object(sr, '_teamviewer_gui_running', return_value=False), \
             patch('routes.system_routes.time.time', side_effect=[0, 99]):
            assert sr._wait_for_teamviewer_gui(timeout=5) is False


class TestLaunchTeamviewerGui:
    @pytest.mark.unit
    def test_already_running_is_a_no_op(self):
        with patch.object(sr, '_teamviewer_gui_running', return_value=True):
            assert sr._launch_teamviewer_gui() == (True, 'already running')

    @pytest.mark.unit
    def test_without_a_graphical_session_it_cannot_start(self):
        with patch.object(sr, '_teamviewer_gui_running', return_value=False), \
             patch.object(sr, '_graphical_session', return_value=None):
            ok, detail = sr._launch_teamviewer_gui()

        assert ok is False
        assert detail == 'no active graphical session'

    @pytest.mark.unit
    def test_root_activates_through_runuser_in_the_user_session(self, as_root):
        session = {'user': 'visioncell', 'uid': '1000', 'type': 'x11'}
        with patch.object(sr, '_teamviewer_gui_running', return_value=False), \
             patch.object(sr, '_graphical_session', return_value=session), \
             patch.object(sr, '_run_cmd', return_value=(0, '', '')) as run, \
             patch.object(sr, '_wait_for_teamviewer_gui', return_value=True):
            ok, detail = sr._launch_teamviewer_gui()

        assert ok is True
        assert 'x11 session for user visioncell' in detail

        cmd = run.call_args[0][0]
        assert cmd[:5] == ['runuser', '-u', 'visioncell', '--', 'env']
        # The bus address must point at the logged-in user's runtime dir, not
        # root's, or dbus activation silently targets the wrong session.
        assert 'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus' in cmd
        assert 'XDG_RUNTIME_DIR=/run/user/1000' in cmd
        assert 'string:' + sr.TEAMVIEWER_DBUS_NAME in cmd

    @pytest.mark.unit
    def test_non_root_activates_in_its_own_session(self, as_user):
        session = {'user': 'visioncell', 'uid': '1000', 'type': 'x11'}
        with patch.object(sr, '_teamviewer_gui_running', return_value=False), \
             patch.object(sr, '_graphical_session', return_value=session), \
             patch.object(sr, '_run_cmd', return_value=(0, '', '')) as run, \
             patch.object(sr, '_wait_for_teamviewer_gui', return_value=True):
            sr._launch_teamviewer_gui()

        assert run.call_args[0][0][0] == 'env'
        assert 'runuser' not in run.call_args[0][0]

    @pytest.mark.unit
    def test_dbus_failure_reports_stderr(self, as_root):
        session = {'user': 'visioncell', 'uid': '1000', 'type': 'x11'}
        with patch.object(sr, '_teamviewer_gui_running', return_value=False), \
             patch.object(sr, '_graphical_session', return_value=session), \
             patch.object(sr, '_run_cmd', return_value=(1, '', 'no such service')):
            ok, detail = sr._launch_teamviewer_gui()

        assert ok is False
        assert 'dbus activation failed: no such service' in detail

    @pytest.mark.unit
    def test_dbus_failure_falls_back_to_the_exit_code(self, as_root):
        session = {'user': 'visioncell', 'uid': '1000', 'type': 'x11'}
        with patch.object(sr, '_teamviewer_gui_running', return_value=False), \
             patch.object(sr, '_graphical_session', return_value=session), \
             patch.object(sr, '_run_cmd', return_value=(7, '', '')):
            ok, detail = sr._launch_teamviewer_gui()

        assert ok is False
        assert 'exit 7' in detail

    @pytest.mark.unit
    def test_activation_that_never_produces_a_gui_is_a_failure(self, as_root):
        # dbus returning 0 is not proof the GUI came up, and reporting success
        # here would leave the device silently unreachable.
        session = {'user': 'visioncell', 'uid': '1000', 'type': 'x11'}
        with patch.object(sr, '_teamviewer_gui_running', return_value=False), \
             patch.object(sr, '_graphical_session', return_value=session), \
             patch.object(sr, '_run_cmd', return_value=(0, '', '')), \
             patch.object(sr, '_wait_for_teamviewer_gui', return_value=False):
            ok, detail = sr._launch_teamviewer_gui()

        assert ok is False
        assert detail == 'dbus activation returned but the GUI did not start'


class TestTeamviewerId:
    @pytest.mark.unit
    def test_parses_the_id_from_info_output(self):
        out = 'TeamViewer 15.4\n TeamViewer ID: 1 234 567 890\n'
        with patch.object(sr, '_run_cmd', return_value=(0, out, '')):
            assert sr._teamviewer_id() == '1 234 567 890'

    @pytest.mark.unit
    def test_strips_ansi_colour_codes(self):
        out = 'TeamViewer ID: \x1b[0;32m1234567890\x1b[0m'
        with patch.object(sr, '_run_cmd', return_value=(0, out, '')):
            assert sr._teamviewer_id() == '1234567890'

    @pytest.mark.unit
    def test_failed_command_returns_none(self):
        with patch.object(sr, '_run_cmd', return_value=(1, '', 'not installed')):
            assert sr._teamviewer_id() is None

    @pytest.mark.unit
    def test_output_without_an_id_returns_none(self):
        with patch.object(sr, '_run_cmd', return_value=(0, 'TeamViewer 15.4\n', '')):
            assert sr._teamviewer_id() is None


class TestStartTeamviewerDaemon:
    @pytest.mark.unit
    def test_already_running_short_circuits(self):
        with patch.object(sr, '_teamviewer_running', return_value=True), \
             patch.object(sr, '_run_cmd') as run:
            assert sr._start_teamviewer_daemon() == (True, 'already_running', '')
        run.assert_not_called()

    @pytest.mark.unit
    def test_unmasks_before_enabling(self, as_root):
        with patch.object(sr, '_teamviewer_running', return_value=False), \
             patch.object(sr, '_run_cmd', return_value=(0, '', '')) as run, \
             patch.object(sr, '_wait_for_teamviewer', return_value=True):
            sr._start_teamviewer_daemon()

        assert run.call_args_list[0][0][0] == \
            ['systemctl', 'unmask', sr.TEAMVIEWER_SERVICE]

    @pytest.mark.unit
    def test_systemctl_enable_now_is_the_first_start_attempt(self, as_root):
        with patch.object(sr, '_teamviewer_running', return_value=False), \
             patch.object(sr, '_run_cmd', return_value=(0, '', '')) as run, \
             patch.object(sr, '_wait_for_teamviewer', return_value=True):
            ok, status, detail = sr._start_teamviewer_daemon()

        assert (ok, status, detail) == (True, 'started', '')
        assert run.call_args_list[1][0][0] == \
            ['systemctl', 'enable', '--now', sr.TEAMVIEWER_SERVICE]

    @pytest.mark.unit
    def test_falls_back_to_the_teamviewer_daemon_command(self, as_root):
        # unmask, systemctl (fails), teamviewer --daemon start (succeeds)
        answers = [(0, '', ''), (1, '', 'unit masked'), (0, '', '')]
        with patch.object(sr, '_teamviewer_running', return_value=False), \
             patch.object(sr, '_run_cmd', side_effect=answers) as run, \
             patch.object(sr, '_wait_for_teamviewer', return_value=True):
            ok, status, _ = sr._start_teamviewer_daemon()

        assert (ok, status) == (True, 'started')
        assert run.call_args_list[2][0][0] == ['teamviewer', '--daemon', 'start']

    @pytest.mark.unit
    def test_a_command_that_exits_zero_but_never_starts_is_not_success(self, as_root):
        # systemctl can return 0 and leave the unit dead; both attempts are
        # tried before giving up.
        with patch.object(sr, '_teamviewer_running', return_value=False), \
             patch.object(sr, '_run_cmd', return_value=(0, '', '')), \
             patch.object(sr, '_wait_for_teamviewer', return_value=False):
            ok, status, detail = sr._start_teamviewer_daemon()

        assert (ok, status) == (False, 'failed')
        assert 'systemctl enable --now' in detail
        assert 'teamviewer --daemon start' in detail

    @pytest.mark.unit
    def test_all_attempts_failing_reports_both_errors(self, as_root):
        answers = [(0, '', ''), (1, '', 'masked'), (127, '', 'not found')]
        with patch.object(sr, '_teamviewer_running', return_value=False), \
             patch.object(sr, '_run_cmd', side_effect=answers), \
             patch.object(sr, '_wait_for_teamviewer', return_value=False):
            ok, status, detail = sr._start_teamviewer_daemon()

        assert ok is False
        assert 'masked' in detail and 'not found' in detail
        assert ' | ' in detail


class TestStartTeamviewerEndpoint:
    @pytest.fixture
    def client(self):
        from flask import Flask
        from flask_restx import Api
        app = Flask(__name__)
        app.config['TESTING'] = True
        api = Api(app)
        with patch('settings.config', {'latest_stable_ref': 'test_version', 'use_aws': False}):
            sr.register_routes(api)
        return app.test_client()

    @pytest.mark.integration
    def test_daemon_and_gui_up_returns_200_with_the_id(self, client):
        with patch.object(sr, '_start_teamviewer_daemon', return_value=(True, 'started', '')), \
             patch.object(sr, '_launch_teamviewer_gui', return_value=(True, 'activated')), \
             patch.object(sr, '_teamviewer_id', return_value='1234567890'):
            response = client.get('/start_teamviewer')

        assert response.status_code == 200
        assert response.get_json() == {
            'success': True, 'status': 'started', 'daemon': 'running',
            'gui': 'activated', 'teamviewer_id': '1234567890'}

    @pytest.mark.integration
    def test_daemon_failure_returns_500(self, client):
        with patch.object(sr, '_start_teamviewer_daemon',
                          return_value=(False, 'failed', 'unit not found')), \
             patch.object(sr, '_launch_teamviewer_gui') as gui:
            response = client.get('/start_teamviewer')

        assert response.status_code == 500
        assert response.get_json() == {
            'success': False, 'status': 'failed', 'error': 'unit not found'}
        gui.assert_not_called()

    @pytest.mark.integration
    def test_daemon_up_but_gui_down_returns_503(self, client):
        # The device is still offline, so this must not read as success.
        with patch.object(sr, '_start_teamviewer_daemon', return_value=(True, 'started', '')), \
             patch.object(sr, '_launch_teamviewer_gui',
                          return_value=(False, 'no active graphical session')), \
             patch.object(sr, '_teamviewer_id', return_value='1234567890'):
            response = client.get('/start_teamviewer')

        assert response.status_code == 503
        body = response.get_json()
        assert body['success'] is False
        assert body['daemon'] == 'running'
        assert 'stay offline' in body['error']

    @pytest.mark.integration
    def test_unexpected_exception_returns_500(self, client):
        with patch.object(sr, '_start_teamviewer_daemon', side_effect=RuntimeError('boom')):
            response = client.get('/start_teamviewer')

        assert response.status_code == 500
        assert response.get_json() == {
            'success': False, 'status': 'error', 'error': 'boom'}

    @pytest.mark.integration
    def test_missing_id_is_reported_as_null_not_an_error(self, client):
        with patch.object(sr, '_start_teamviewer_daemon', return_value=(True, 'started', '')), \
             patch.object(sr, '_launch_teamviewer_gui', return_value=(True, 'activated')), \
             patch.object(sr, '_teamviewer_id', return_value=None):
            response = client.get('/start_teamviewer')

        assert response.status_code == 200
        assert response.get_json()['teamviewer_id'] is None
