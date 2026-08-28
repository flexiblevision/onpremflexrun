"""Power, backend-restart and flex-run upgrade endpoints.

These are the endpoints that take a factory-floor machine off the line:
/shutdown and /restart cut power to it, /refresh_backend drops the camera
locks, /upgrade_flex_run replaces the code this very server is running from.
Each one is one HTTP call away from a caller who guessed the URL.
"""
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


class TestRestartBackend:
    """capdev is stopped, cameras released, vision restarted, capdev started."""

    @pytest.mark.integration
    def test_restarts_capdev_and_vision_in_order(self, client, no_sleep):
        ready = MagicMock(status_code=200)
        ready.json.return_value = [{'id': 'cam0'}]

        with patch('os.system') as system, \
             patch('requests.get', return_value=ready):
            response = client.get('/refresh_backend')

        assert response.status_code == 200
        assert system.call_args_list == [
            call('docker restart capdev'),
            call('docker restart vision'),
            call('docker start capdev'),
        ]

    @pytest.mark.integration
    def test_releases_camera_locks_before_restarting_vision(self, client, no_sleep):
        ready = MagicMock(status_code=200)
        ready.json.return_value = [{'id': 'cam0'}]

        with patch('os.system'), patch('requests.get', return_value=ready) as get:
            client.get('/refresh_backend')

        assert get.call_args_list[0][0][0].endswith('/releaseAll')

    @pytest.mark.integration
    def test_starts_capdev_even_when_release_all_fails(self, client, no_sleep):
        # A vision container that is already down must not strand capdev in the
        # stopped state.
        ready = MagicMock(status_code=200)
        ready.json.return_value = [{'id': 'cam0'}]

        def get(url, **kw):
            if url.endswith('/releaseAll'):
                raise ConnectionError('vision is down')
            return ready

        with patch('os.system') as system, patch('requests.get', side_effect=get):
            response = client.get('/refresh_backend')

        assert response.status_code == 200
        assert call('docker start capdev') in system.call_args_list

    @pytest.mark.integration
    def test_stops_polling_as_soon_as_cameras_appear(self, client, no_sleep):
        empty = MagicMock(status_code=200)
        empty.json.return_value = []
        found = MagicMock(status_code=200)
        found.json.return_value = [{'id': 'cam0'}, {'id': 'cam1'}]
        responses = [MagicMock(status_code=200), empty, empty, found]

        with patch('os.system'), patch('requests.get', side_effect=responses):
            client.get('/refresh_backend')

        # releaseAll + three /cameras polls, then it stops rather than
        # burning the full 120s budget.
        assert no_sleep.call_count == 3

    @pytest.mark.integration
    def test_gives_up_after_the_timeout_and_starts_capdev_anyway(self, client, no_sleep):
        empty = MagicMock(status_code=200)
        empty.json.return_value = []

        with patch('os.system') as system, patch('requests.get', return_value=empty):
            response = client.get('/refresh_backend')

        assert response.status_code == 200
        # 120s budget on a 5s interval.
        assert no_sleep.call_count == 24
        assert system.call_args_list[-1] == call('docker start capdev')

    @pytest.mark.integration
    def test_unreachable_vision_does_not_abort_the_restart(self, client, no_sleep):
        with patch('os.system') as system, \
             patch('requests.get', side_effect=ConnectionError('refused')):
            response = client.get('/refresh_backend')

        assert response.status_code == 200
        assert call('docker start capdev') in system.call_args_list

    @pytest.mark.integration
    def test_non_200_from_cameras_keeps_polling(self, client, no_sleep):
        with patch('os.system'), patch('requests.get', return_value=MagicMock(status_code=503)):
            client.get('/refresh_backend')

        assert no_sleep.call_count == 24


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
