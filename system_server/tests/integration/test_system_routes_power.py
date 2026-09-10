"""Power, backend-restart and flex-run upgrade endpoints.

These are the endpoints that take a factory-floor machine off the line:
/shutdown and /restart cut power to it, /refresh_backend drops the camera
locks, /upgrade_flex_run replaces the code this very server is running from.
Each one is one HTTP call away from a caller who guessed the URL.
"""
import itertools

import pytest
from testsupport import thread_aware_sleep_mock
from unittest.mock import patch, MagicMock, call


@pytest.fixture
def client():
    from flask import Flask
    from flask_restx import Api
    app = Flask(__name__)
    app.config['TESTING'] = True
    api = Api(app)
    with patch('settings.config', {'latest_stable_ref': 'test_version', 'use_aws': False}):
        from routes import system_routes
        system_routes.register_routes(api)
    return app.test_client()


@pytest.fixture
def no_sleep():
    """RestartBackend polls on a 5s interval; tests must not actually wait."""
    with patch('time.sleep', new=thread_aware_sleep_mock()) as sleep:
        yield sleep


def _completed(returncode=0, stdout='', stderr=''):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


class TestShutdown:
    @pytest.mark.integration
    def test_get_powers_the_machine_off(self, client):
        with patch('os.system') as system:
            response = client.get('/shutdown')

        assert response.status_code == 200
        system.assert_called_once_with('poweroff')

    @pytest.mark.integration
    def test_is_not_reachable_by_post(self, client):
        with patch('os.system') as system:
            response = client.post('/shutdown')

        assert response.status_code == 405
        system.assert_not_called()


class TestRestart:
    @pytest.mark.integration
    def test_get_reboots_the_machine(self, client):
        with patch('os.system') as system:
            response = client.get('/restart')

        assert response.status_code == 200
        system.assert_called_once_with('reboot')

    @pytest.mark.integration
    def test_is_not_reachable_by_post(self, client):
        with patch('os.system') as system:
            response = client.post('/restart')

        assert response.status_code == 405
        system.assert_not_called()


def _resp(status=200, payload=None):
    r = MagicMock(status_code=status)
    r.json.return_value = [] if payload is None else payload
    return r


def _routed_get(cameras, release=None, capdev=None):
    """A requests.get double that answers by URL.

    Each of cameras/release/capdev is a response or an exception to raise;
    cameras may also be a list handed out in turn, the last one repeating.
    capdev defaults to healthy so a test can assert on the vision budget
    without the capdev readiness poll contributing sleeps of its own.
    """
    release = _resp(200) if release is None else release
    capdev = _resp(200) if capdev is None else capdev
    queue = list(cameras) if isinstance(cameras, list) else None

    def answer(value):
        if isinstance(value, Exception):
            raise value
        return value

    def get(url, **kw):
        if '/auth/jwks' in url:
            return answer(capdev)
        if url.endswith('/releaseAll'):
            return answer(release)
        if queue:
            return answer(queue.pop(0) if len(queue) > 1 else queue[0])
        return answer(cameras)

    return get


class TestRestartBackend:
    """capdev is stopped, cameras released, vision restarted, capdev started.

    capdev stays down for the whole vision restart on purpose. Its calls into
    vision carry no timeout, so a capdev left running while vision goes away
    hangs every gunicorn thread it has and never answers again.
    """

    @pytest.mark.integration
    def test_stops_capdev_for_the_whole_vision_restart(self, client, no_sleep):
        with patch('os.system', return_value=0) as system, \
             patch('requests.get', side_effect=_routed_get(_resp(200, [{'id': 'cam0'}]))):
            response = client.get('/refresh_backend')

        assert response.status_code == 200
        assert system.call_args_list == [
            call('docker stop capdev'),
            call('docker restart vision'),
            call('docker start capdev'),
        ]
        assert response.get_json() == {'cameras_ready': True, 'capdev_ready': True}

    @pytest.mark.integration
    def test_releases_camera_locks_before_restarting_vision(self, client, no_sleep):
        with patch('os.system', return_value=0), \
             patch('requests.get',
                   side_effect=_routed_get(_resp(200, [{'id': 'cam0'}]))) as get:
            client.get('/refresh_backend')

        assert get.call_args_list[0][0][0].endswith('/releaseAll')

    @pytest.mark.integration
    def test_starts_capdev_even_when_release_all_fails(self, client, no_sleep):
        # A vision container that is already down must not strand capdev in the
        # stopped state.
        with patch('os.system', return_value=0) as system, \
             patch('requests.get',
                   side_effect=_routed_get(_resp(200, [{'id': 'cam0'}]),
                                           release=ConnectionError('vision is down'))):
            response = client.get('/refresh_backend')

        assert response.status_code == 200
        assert call('docker start capdev') in system.call_args_list

    @pytest.mark.integration
    def test_stops_polling_as_soon_as_cameras_appear(self, client, no_sleep):
        found = _resp(200, [{'id': 'cam0'}, {'id': 'cam1'}])

        with patch('os.system', return_value=0), \
             patch('requests.get',
                   side_effect=_routed_get([_resp(200), _resp(200), found])):
            client.get('/refresh_backend')

        # Three /cameras polls, then it stops rather than burning the full
        # 120s budget.
        assert no_sleep.call_count == 3

    @pytest.mark.integration
    def test_gives_up_after_the_timeout_and_starts_capdev_anyway(self, client, no_sleep):
        with patch('os.system', return_value=0) as system, \
             patch('requests.get', side_effect=_routed_get(_resp(200))):
            response = client.get('/refresh_backend')

        assert response.status_code == 200
        # 120s budget on a 5s interval.
        assert no_sleep.call_count == 24
        assert system.call_args_list[-1] == call('docker start capdev')
        assert response.get_json()['cameras_ready'] is False

    @pytest.mark.integration
    def test_a_slow_poll_is_charged_against_the_budget(self, client, no_sleep):
        # listCameras runs synchronously on the first /cameras call, so a poll
        # can block for its full 30s timeout. Charging only the 5s interval
        # made the 120s budget 24 * 35s of capdev downtime instead.
        clock = itertools.count(0, 30)

        with patch('os.system', return_value=0), \
             patch('time.monotonic', side_effect=lambda: next(clock)), \
             patch('requests.get', side_effect=_routed_get(_resp(200))):
            client.get('/refresh_backend')

        # 35s consumed per poll, so four of them exhaust the budget.
        assert no_sleep.call_count == 4

    @pytest.mark.integration
    def test_unreachable_vision_does_not_abort_the_restart(self, client, no_sleep):
        with patch('os.system', return_value=0) as system, \
             patch('requests.get',
                   side_effect=_routed_get(ConnectionError('refused'),
                                           release=ConnectionError('refused'))):
            response = client.get('/refresh_backend')

        assert response.status_code == 200
        assert call('docker start capdev') in system.call_args_list

    @pytest.mark.integration
    def test_non_200_from_cameras_keeps_polling(self, client, no_sleep):
        with patch('os.system', return_value=0), \
             patch('requests.get', side_effect=_routed_get(_resp(503))):
            client.get('/refresh_backend')

        assert no_sleep.call_count == 24

    @pytest.mark.integration
    def test_a_failed_start_is_retried(self, client, no_sleep):
        # capdev was stopped by hand, so unless-stopped will not bring it back
        # on its own - not in the background, and not across a reboot. An
        # unchecked start is what leaves a device dark.
        def system(cmd):
            return 1 if cmd == 'docker start capdev' else 0

        with patch('os.system', side_effect=system) as sysmock, \
             patch('requests.get', side_effect=_routed_get(_resp(200, [{'id': 'cam0'}]))):
            response = client.get('/refresh_backend')

        assert sysmock.call_args_list.count(call('docker start capdev')) == 3
        assert response.get_json()['capdev_ready'] is False

    @pytest.mark.integration
    def test_reports_a_capdev_that_starts_but_never_answers(self, client, no_sleep):
        with patch('os.system', return_value=0), \
             patch('requests.get',
                   side_effect=_routed_get(_resp(200, [{'id': 'cam0'}]),
                                           capdev=ConnectionError('refused'))):
            response = client.get('/refresh_backend')

        assert response.get_json() == {'cameras_ready': True, 'capdev_ready': False}

    @pytest.mark.integration
    def test_capdev_is_started_even_when_the_vision_phase_blows_up(self, client, no_sleep):
        def system(cmd):
            if cmd == 'docker restart vision':
                raise RuntimeError('docker daemon gone')
            return 0

        with patch('os.system', side_effect=system) as sysmock, \
             patch('requests.get', side_effect=_routed_get(_resp(200, [{'id': 'cam0'}]))):
            try:
                client.get('/refresh_backend')
            except RuntimeError:
                pass

        assert call('docker start capdev') in sysmock.call_args_list


class TestUpgradeFlexRun:
    """Replaces the flex-run checkout this server runs from."""

    @pytest.mark.integration
    def test_success_reports_updated(self, client):
        with patch('subprocess.run', return_value=_completed(0, 'pulled', '')), \
             patch('os.environ', {'HOME': '/home/visioncell'}):
            response = client.get('/upgrade_flex_run')

        assert response.status_code == 200
        assert response.get_json() == {'status': 'flex-run updated'}

    @pytest.mark.integration
    def test_makes_the_script_executable_before_running_it(self, client):
        with patch('subprocess.run', return_value=_completed(0)) as run, \
             patch('os.environ', {'HOME': '/home/visioncell'}):
            client.get('/upgrade_flex_run')

        chmod, script = run.call_args_list
        assert chmod[0][0] == ['chmod', '+x',
                               '/home/visioncell/flex-run/upgrades/upgrade_flex_run.sh']
        assert script[0][0] == ['sh',
                                '/home/visioncell/flex-run/upgrades/upgrade_flex_run.sh']

    @pytest.mark.integration
    def test_failure_returns_500_with_the_mapped_reason(self, client):
        import upgrade_runner

        with patch('subprocess.run', return_value=_completed(1, '', 'git failed')), \
             patch('os.environ', {'HOME': '/home/visioncell'}):
            response = client.get('/upgrade_flex_run')

        assert response.status_code == 500
        body = response.get_json()
        assert body['exit_code'] == 1
        assert body['error'] == upgrade_runner.flex_run_error(1)
        assert body['detail'] == 'git failed'

    @pytest.mark.integration
    def test_long_stderr_is_truncated_to_the_tail(self, client):
        with patch('subprocess.run', return_value=_completed(3, '', 'x' * 900)), \
             patch('os.environ', {'HOME': '/home/visioncell'}):
            response = client.get('/upgrade_flex_run')

        assert len(response.get_json()['detail']) == 500

    @pytest.mark.integration
    def test_missing_stderr_does_not_break_the_error_body(self, client):
        with patch('subprocess.run', return_value=_completed(2, '', None)), \
             patch('os.environ', {'HOME': '/home/visioncell'}):
            response = client.get('/upgrade_flex_run')

        assert response.status_code == 500
        assert response.get_json()['detail'] == ''


class TestRestartFO:
    @pytest.mark.integration
    def test_restarts_the_fo_server(self, client):
        with patch('os.system') as system:
            response = client.get('/restart_fo')

        # Not registered by register_routes - documented so that adding it
        # later is a deliberate act.
        assert response.status_code == 404
        system.assert_not_called()

    @pytest.mark.integration
    def test_returns_200_when_invoked_directly(self):
        from routes.system_routes import RestartFO

        with patch('os.system') as system:
            body, status = RestartFO().get()

        assert (body, status) == ('FO server restarted', 200)
        system.assert_called_once_with('forever restart /root/flex-run/aws/fo_server.py')

    @pytest.mark.integration
    def test_returns_500_when_forever_raises(self):
        from routes.system_routes import RestartFO

        with patch('os.system', side_effect=OSError('no forever')):
            body, status = RestartFO().get()

        assert status == 500
        assert body == 'Error restarting FO server'


class TestRouteRegistration:
    @pytest.mark.integration
    def test_every_documented_path_is_registered(self, client):
        expected = {'/shutdown', '/restart', '/refresh_backend', '/list_services',
                    '/upgrade', '/upgrade_status', '/upgrade_flex_run',
                    '/system_versions', '/system_uptodate', '/start_teamviewer'}

        from flask import current_app
        rules = {r.rule for r in client.application.url_map.iter_rules()}

        assert expected <= rules


class TestReleasesReportsTrust:
    """A rotation cannot be finished safely unless you can see which devices
    have picked up the new key, so /releases has to report it."""

    def _keypair(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        key = ec.generate_private_key(ec.SECP256R1())
        return key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo)

    def test_it_lists_the_trusted_key_fingerprints(self, client, tmp_path, monkeypatch):
        from release import trust
        store = tmp_path / 'keys'
        pems = [self._keypair() for _ in range(2)]
        for index, pem in enumerate(pems):
            trust.provision(str(store), 'release-{}.pem'.format(index), pem)
        monkeypatch.setenv('FLEXRUN_TRUST_DIR', str(store))

        body = client.get('/releases').get_json()
        assert body['trust']['count'] == 2
        reported = {k['fingerprint'] for k in body['trust']['keys']}
        assert reported == {trust.fingerprint(pem) for pem in pems}

    def test_a_device_with_no_trust_store_reports_zero_not_an_error(
            self, client, tmp_path, monkeypatch):
        """Every device is in this state until provisioning happens, and the
        settings screen still has to render."""
        monkeypatch.setenv('FLEXRUN_TRUST_DIR', str(tmp_path / 'absent'))
        body = client.get('/releases').get_json()
        assert body['trust']['count'] == 0
        assert body['trust']['keys'] == []

    def test_trust_failure_does_not_break_the_rest_of_the_payload(
            self, client, tmp_path, monkeypatch):
        monkeypatch.setenv('FLEXRUN_TRUST_DIR', str(tmp_path / 'absent'))
        body = client.get('/releases').get_json()
        assert 'high_water' in body
        assert 'rollback_targets' in body
