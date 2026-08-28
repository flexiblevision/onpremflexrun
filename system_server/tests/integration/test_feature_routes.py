"""FTP account management and Time Machine install.

These endpoints create Linux users and delete home directories. The interesting
cases are the guards: a missing key that falls through to running the command
anyway, and a delete that builds a path from unvalidated input.

The addon toggles that used to live here (audio, OCR) moved to
test_addon_routes.py along with the routes themselves.
"""
import pytest
from unittest.mock import patch, MagicMock, call

from routes import ftp_routes, timemachine_routes


def _api_client(module):
    from flask import Flask
    from flask_restx import Api
    app = Flask(__name__)
    app.config['TESTING'] = True
    api = Api(app)
    module.register_routes(api)
    return app.test_client()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    return str(tmp_path)


# --------------------------------------------------------------------------
# FTP user management
# --------------------------------------------------------------------------

@pytest.fixture
def ftp_client():
    return _api_client(ftp_routes)


class TestAddFtpUser:
    @pytest.mark.integration
    def test_creates_the_account(self, ftp_client, home):
        with patch('subprocess.call') as run:
            response = ftp_client.post('/add_ftp_user',
                                       json={'username': 'line3', 'password': 'pw'})

        assert response.get_json() is True
        run.assert_called_once_with(
            ['sh', home + '/flex-run/scripts/add_ftp_user.sh', 'line3', 'pw'])

    @pytest.mark.integration
    def test_a_payload_without_a_password_is_refused(self, ftp_client, home):
        with patch('subprocess.call') as run:
            response = ftp_client.post('/add_ftp_user', json={'username': 'line3'})

        assert response.get_json() is False
        run.assert_not_called()

    @pytest.mark.integration
    def test_a_payload_without_a_username_still_runs(self, ftp_client, home):
        # `if 'username' and 'password' in data` only tests for 'password':
        # the first operand is a non-empty string constant, always truthy. A
        # payload with a password and no username therefore reaches the script
        # and raises. Pinned because the condition reads as though it checks
        # both.
        with patch('subprocess.call') as run:
            with pytest.raises(KeyError):
                ftp_client.post('/add_ftp_user', json={'password': 'pw'})

        run.assert_not_called()


class TestDeleteFtpUser:
    @pytest.mark.integration
    def test_removes_the_account_and_its_home_directory(self, ftp_client):
        with patch('os.system') as system:
            response = ftp_client.delete('/delete_ftp_user', json={'username': 'line3'})

        assert response.get_json() is True
        assert system.call_args_list == [call('sudo deluser -f line3'),
                                          call('sudo rm -r /home/line3')]

    @pytest.mark.integration
    def test_a_payload_without_a_username_is_refused(self, ftp_client):
        with patch('os.system') as system:
            response = ftp_client.delete('/delete_ftp_user', json={'other': 'x'})

        assert response.get_json() is False
        system.assert_not_called()

    @pytest.mark.integration
    def test_the_username_is_interpolated_into_a_shell_command_unescaped(self, ftp_client):
        # This endpoint is behind requires_auth, but the username reaches
        # `sudo rm -r /home/<value>` through os.system with no quoting or
        # validation. Recorded so the exposure is visible rather than implied.
        with patch('os.system') as system:
            ftp_client.delete('/delete_ftp_user', json={'username': 'a; touch /tmp/x'})

        assert system.call_args_list[1] == call('sudo rm -r /home/a; touch /tmp/x')


class TestUpdateFtpPort:
    @pytest.mark.integration
    def test_writes_the_new_listen_port(self, ftp_client, home):
        with patch('subprocess.call') as run:
            response = ftp_client.put('/update_ftp_port', json={'port': 2121})

        assert response.get_json() is True
        run.assert_called_once_with(
            ['sh', home + '/flex-run/scripts/update_ftp.sh', 'listen_port', '2121'])

    @pytest.mark.integration
    def test_a_string_port_is_accepted(self, ftp_client, home):
        with patch('subprocess.call') as run:
            ftp_client.put('/update_ftp_port', json={'port': '2121'})
        assert run.call_args[0][0][-1] == '2121'

    @pytest.mark.integration
    @pytest.mark.parametrize('port', [0, -1])
    def test_a_non_positive_port_is_not_written(self, ftp_client, home, port):
        with patch('subprocess.call') as run:
            response = ftp_client.put('/update_ftp_port', json={'port': port})

        # Still reports success, but nothing was changed.
        assert response.get_json() is True
        run.assert_not_called()

    @pytest.mark.integration
    def test_a_payload_without_a_port_is_refused(self, ftp_client, home):
        with patch('subprocess.call') as run:
            assert ftp_client.put('/update_ftp_port', json={}).get_json() is False
        run.assert_not_called()

    @pytest.mark.integration
    def test_a_non_numeric_port_raises(self, ftp_client, home):
        with patch('subprocess.call'):
            with pytest.raises(ValueError):
                ftp_client.put('/update_ftp_port', json={'port': 'ftp'})


class TestEnableFtp:
    @pytest.mark.integration
    def test_runs_the_server_setup_script(self, ftp_client, home):
        with patch('subprocess.call') as run:
            response = ftp_client.post('/enable_ftp', json={'port': 21})

        assert response.get_json() is True
        run.assert_called_once_with(['sh', home + '/flex-run/setup/ftp_server_setup.sh'])

    @pytest.mark.integration
    def test_a_payload_without_a_port_is_refused(self, ftp_client, home):
        with patch('subprocess.call') as run:
            assert ftp_client.post('/enable_ftp', json={}).get_json() is False
        run.assert_not_called()


class TestFtpRouteRegistration:
    @pytest.mark.integration
    def test_every_path_is_registered(self, ftp_client):
        rules = {r.rule for r in ftp_client.application.url_map.iter_rules()}
        assert {'/add_ftp_user', '/delete_ftp_user',
                '/update_ftp_port', '/enable_ftp'} <= rules


# --------------------------------------------------------------------------
# Time machine
# --------------------------------------------------------------------------

@pytest.fixture
def tm_client():
    return _api_client(timemachine_routes)


@pytest.fixture
def authorized():
    with patch.object(timemachine_routes, 'validate_account', return_value=True) as v:
        yield v


TOKEN = {'Access-Token': 'tok'}


class TestEnableTimemachine:
    @pytest.mark.integration
    @pytest.mark.parametrize('tm_type', ['local', 'zip_push'])
    def test_local_types_queue_the_installer(self, tm_client, authorized, tm_type):
        with patch.object(timemachine_routes.job_queue, 'enqueue',
                          return_value=MagicMock(id='job-1')) as enqueue, \
             patch.object(timemachine_routes, 'insert_job') as insert:
            response = tm_client.post('/enable_timemachine',
                                      json={'type': tm_type}, headers=TOKEN)

        assert response.status_code == 200
        assert enqueue.call_args[0][:2] == (
            timemachine_routes.local_zip_push_install, tm_type)
        insert.assert_called_once_with('job-1', 'installing time machine locally')

    @pytest.mark.integration
    def test_cloud_type_installs_inline(self, tm_client, authorized):
        with patch.object(timemachine_routes, 'cloud_install', return_value=True) as install, \
             patch.object(timemachine_routes.job_queue, 'enqueue') as enqueue:
            response = tm_client.post('/enable_timemachine',
                                      json={'type': 'cloud'}, headers=TOKEN)

        assert response.status_code == 200
        install.assert_called_once()
        enqueue.assert_not_called()

    @pytest.mark.integration
    def test_a_failed_cloud_install_is_a_500(self, tm_client, authorized):
        with patch.object(timemachine_routes, 'cloud_install', return_value=False):
            response = tm_client.post('/enable_timemachine',
                                      json={'type': 'cloud'}, headers=TOKEN)

        assert response.status_code == 500
        assert response.get_json() is False

    @pytest.mark.integration
    def test_a_missing_access_token_is_a_403(self, tm_client):
        with patch.object(timemachine_routes, 'validate_account') as validate:
            response = tm_client.post('/enable_timemachine', json={'type': 'local'})

        assert response.status_code == 403
        # The entitlement check is not even reached.
        validate.assert_not_called()

    @pytest.mark.integration
    def test_an_unentitled_account_is_a_403(self, tm_client):
        with patch.object(timemachine_routes, 'validate_account', return_value=False), \
             patch.object(timemachine_routes.job_queue, 'enqueue') as enqueue:
            response = tm_client.post('/enable_timemachine',
                                      json={'type': 'local'}, headers=TOKEN)

        assert response.status_code == 403
        enqueue.assert_not_called()

    @pytest.mark.integration
    def test_the_entitlement_is_checked_against_the_caller_token(self, tm_client, authorized):
        with patch.object(timemachine_routes, 'cloud_install', return_value=True):
            tm_client.post('/enable_timemachine', json={'type': 'cloud'}, headers=TOKEN)

        authorized.assert_called_once_with('time_machine', 'tok')

    @pytest.mark.integration
    def test_an_unknown_type_is_a_500(self, tm_client, authorized):
        with patch.object(timemachine_routes.job_queue, 'enqueue') as enqueue:
            response = tm_client.post('/enable_timemachine',
                                      json={'type': 'magnetic-tape'}, headers=TOKEN)

        assert response.status_code == 500
        assert 'must be one of' in response.get_json()
        enqueue.assert_not_called()

    @pytest.mark.integration
    def test_a_missing_type_is_a_500(self, tm_client, authorized):
        response = tm_client.post('/enable_timemachine', json={}, headers=TOKEN)

        assert response.status_code == 500
        assert 'missing type key' in response.get_json()


class TestDisableTimemachine:
    @pytest.mark.integration
    @pytest.mark.parametrize('tm_type', ['local', 'zip_push'])
    def test_local_types_run_the_uninstaller(self, tm_client, home, tm_type):
        with patch('os.system') as system:
            response = tm_client.delete('/disable_timemachine', json={'type': tm_type})

        assert response.status_code == 200
        system.assert_called_once_with(
            'sh ' + home + '/flex-run/system_server/timemachine/uninstaller.sh')

    @pytest.mark.integration
    def test_cloud_type_runs_nothing_locally(self, tm_client, home):
        with patch('os.system') as system:
            response = tm_client.delete('/disable_timemachine', json={'type': 'cloud'})

        assert response.status_code == 200
        system.assert_not_called()

    @pytest.mark.integration
    def test_a_missing_type_is_a_500(self, tm_client, home):
        with patch('os.system') as system:
            response = tm_client.delete('/disable_timemachine', json={})

        assert response.status_code == 500
        system.assert_not_called()


class TestCleanupTimemachine:
    @pytest.mark.integration
    def test_returns_the_cleanup_result(self, tm_client):
        with patch.object(timemachine_routes, 'cleanup_timemachine_records',
                          return_value={'deleted': 12}) as cleanup:
            response = tm_client.delete('/cleanup_timemachine')

        assert response.status_code == 200
        assert response.get_json() == {'deleted': 12}
        cleanup.assert_called_once()


class TestTimemachineRouteRegistration:
    @pytest.mark.integration
    def test_every_path_is_registered(self, tm_client):
        rules = {r.rule for r in tm_client.application.url_map.iter_rules()}
        assert {'/enable_timemachine', '/disable_timemachine',
                '/cleanup_timemachine'} <= rules
