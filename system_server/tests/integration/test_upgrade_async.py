"""POST /upgrade must enqueue and return, not run the upgrade in the request."""
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def client(tmp_path):
    from flask import Flask
    from flask_restx import Api
    app = Flask(__name__)
    app.config['TESTING'] = True
    api = Api(app)
    with patch('settings.config', {'latest_stable_ref': 'test_version', 'use_aws': False}):
        from routes import system_routes
        system_routes.register_routes(api)
    return app.test_client()


def _docker_ok(cmd, *a, **kw):
    r = MagicMock()
    r.returncode = 0
    r.stdout = 'Username: fvonprem\n'
    r.stderr = ''
    return r


@patch('routes.system_routes.generate_environment_config')
@patch('routes.system_routes.is_container_uptodate', return_value=(False, '1.9.3'))
@patch('auth.requires_auth', lambda f: f)
class TestUpgradeIsAsync:

    def test_post_returns_202_without_running_the_upgrade(self, mock_uptd, mock_cfg, client):
        with patch('subprocess.run', side_effect=_docker_ok) as run, \
             patch('subprocess.Popen') as popen, \
             patch('upgrade_runner.lock_holder', return_value=None), \
             patch('requests.get'):
            resp = client.post('/upgrade')

        assert resp.status_code == 202, resp.status_code
        body = resp.get_json()
        assert body['status'] == 'upgrade started'
        assert body['id'] and body['poll'] == '/upgrade_status'

        # The upgrade must be spawned, never executed inside the request.
        assert popen.called, 'runner was not spawned'
        argv = popen.call_args[0][0]
        assert 'upgrade_runner.py' in ' '.join(argv)
        assert popen.call_args[1]['start_new_session'] is True, \
            'runner must be detached or the server restart truncates it'
        ran = [c[0][0] for c in run.call_args_list]
        assert not any('upgrade_system.sh' in ' '.join(map(str, c)) for c in ran), \
            'upgrade_system.sh ran inside the HTTP request'

    def test_get_still_works_for_older_uis(self, mock_uptd, mock_cfg, client):
        with patch('subprocess.run', side_effect=_docker_ok), \
             patch('subprocess.Popen') as popen, \
             patch('upgrade_runner.lock_holder', return_value=None), \
             patch('requests.get'):
            resp = client.get('/upgrade')
        assert resp.status_code == 202
        assert popen.called

    def test_concurrent_upgrade_is_refused(self, mock_uptd, mock_cfg, client):
        with patch('subprocess.run', side_effect=_docker_ok), \
             patch('subprocess.Popen') as popen, \
             patch('upgrade_runner.lock_holder', return_value=4321), \
             patch('requests.get'):
            resp = client.post('/upgrade')

        assert resp.status_code == 409, resp.status_code
        assert resp.get_json()['pid'] == 4321
        assert not popen.called, 'a second upgrade was spawned while one was running'

    def test_docker_login_still_gates_the_upgrade(self, mock_uptd, mock_cfg, client):
        def not_logged_in(cmd, *a, **kw):
            r = MagicMock()
            r.returncode = 0
            r.stdout = 'Server Version: 24.0\n'
            r.stderr = ''
            return r

        with patch('subprocess.run', side_effect=not_logged_in), \
             patch('subprocess.Popen') as popen, \
             patch('upgrade_runner.lock_holder', return_value=None), \
             patch('requests.get'):
            resp = client.post('/upgrade')

        assert resp.status_code == 403
        assert not popen.called


def test_upgrade_status_endpoint_reports_runner_state(client):
    with patch('upgrade_runner.status', return_value={'state': 'running', 'cur_step': 3}):
        resp = client.get('/upgrade_status')
    assert resp.status_code == 200
    assert resp.get_json()['state'] == 'running'
