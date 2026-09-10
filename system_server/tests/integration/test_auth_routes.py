"""Device credentials and cloud sync.

The refresh token stored by /auth_token is what lets a device talk to the cloud
at all; /deauthorize is what revokes it. /sync_analytics is the push path, and
its job-count guard is the only thing stopping a backlogged device from
enqueuing work faster than the workers can drain it.
"""
import pytest
from unittest.mock import patch, MagicMock, call

from routes import auth_routes


@pytest.fixture
def client():
    from flask import Flask
    from flask_restx import Api
    app = Flask(__name__)
    app.config['TESTING'] = True
    api = Api(app)
    auth_routes.register_routes(api)
    return app.test_client()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    return str(tmp_path)


TOKEN = {'Access-Token': 'tok'}


class TestAuthTokenGet:
    @pytest.mark.integration
    def test_returns_the_stored_credential(self, client, home):
        proc = MagicMock()
        proc.communicate.return_value = (b'  refresh-token-value \n', b'')
        with patch('subprocess.Popen', return_value=proc) as popen:
            response = client.get('/auth_token')

        assert response.get_json() == 'refresh-token-value'
        assert popen.call_args[0][0] == [
            'cat', home + '/flex-run/system_server/creds.txt']

    @pytest.mark.integration
    def test_an_empty_credentials_file_returns_nothing(self, client, home):
        proc = MagicMock()
        proc.communicate.return_value = (b'\n', b'')
        with patch('subprocess.Popen', return_value=proc):
            assert client.get('/auth_token').get_json() is None


class TestAuthTokenPost:
    @pytest.mark.integration
    def test_writes_the_refresh_token(self, client, home):
        with patch('os.system') as system:
            response = client.post('/auth_token', json={'refresh_token': 'abc123'})

        assert response.get_json() is True
        system.assert_called_once_with(
            'echo abc123 > ' + home + '/flex-run/system_server/creds.txt')

    @pytest.mark.integration
    def test_an_empty_body_is_refused(self, client, home):
        with patch('os.system') as system:
            response = client.post('/auth_token', json={})

        assert response.get_json() is False
        system.assert_not_called()

    @pytest.mark.integration
    def test_a_local_master_rewrites_the_cloud_domain(self, client, home):
        config = {'environ': 'local', 'cloud_domain': 'https://old.example'}
        with patch('settings.config', config), \
             patch.object(auth_routes, 'write_settings_to_config') as write, \
             patch('os.system'):
            client.post('/auth_token',
                        json={'refresh_token': 'abc', 'obj': {'server_ip': '10.0.0.5'}})

        assert config['cloud_domain'] == 'http://10.0.0.5'
        write.assert_called_once()

    @pytest.mark.integration
    def test_a_scheme_already_present_is_preserved(self, client, home):
        config = {'environ': 'local'}
        with patch('settings.config', config), \
             patch.object(auth_routes, 'write_settings_to_config'), \
             patch('os.system'):
            client.post('/auth_token',
                        json={'refresh_token': 'abc',
                              'obj': {'server_ip': 'https://master.local'}})

        assert config['cloud_domain'] == 'https://master.local'

    @pytest.mark.integration
    def test_a_cloud_device_never_rewrites_the_domain(self, client, home):
        # Doing so would downgrade the TLS-only cloud host to http and break
        # every sync from that device onwards.
        config = {'environ': 'cloud', 'cloud_domain': 'https://clouddeploy.example'}
        with patch('settings.config', config), \
             patch.object(auth_routes, 'write_settings_to_config') as write, \
             patch('os.system'):
            client.post('/auth_token',
                        json={'refresh_token': 'abc', 'obj': {'server_ip': '10.0.0.5'}})

        assert config['cloud_domain'] == 'https://clouddeploy.example'
        write.assert_not_called()

    @pytest.mark.integration
    def test_a_local_device_without_a_server_ip_does_not_rewrite(self, client, home):
        config = {'environ': 'local', 'cloud_domain': 'https://old.example'}
        with patch('settings.config', config), \
             patch.object(auth_routes, 'write_settings_to_config') as write, \
             patch('os.system'):
            client.post('/auth_token', json={'refresh_token': 'abc', 'obj': {}})

        assert config['cloud_domain'] == 'https://old.example'
        write.assert_not_called()


class TestDeAuthorize:
    @pytest.mark.integration
    def test_removes_the_credentials_file(self, client, home):
        with patch('os.system') as system:
            response = client.get('/deauthorize')

        assert response.status_code == 200
        system.assert_called_once_with(
            'rm ' + home + '/flex-run/system_server/creds.txt')


@pytest.fixture
def sync_env():
    """Everything /sync_analytics fans out to."""
    with patch.object(auth_routes, 'push_analytics_to_cloud') as analytics, \
         patch.object(auth_routes, 'push_assembly_progress') as assembly, \
         patch.object(auth_routes, 'get_unprocessed_events',
                      return_value={'count': 0}) as events, \
         patch.object(auth_routes, 'insert_job') as insert, \
         patch.object(auth_routes.job_queue, 'enqueue',
                      return_value=MagicMock(id='job-1')) as enqueue, \
         patch.object(auth_routes, 'get_cloud_domain',
                      return_value='https://cloud.example'):
        yield {'analytics': analytics, 'assembly': assembly, 'events': events,
               'insert': insert, 'enqueue': enqueue}


class TestSyncAnalytics:
    @pytest.mark.integration
    def test_pushes_analytics_and_assembly_progress(self, client, sync_env):
        with patch.object(type(auth_routes.job_queue), 'count', 0):
            client.get('/sync_analytics', headers=TOKEN)

        sync_env['analytics'].assert_called_once_with('https://cloud.example', 'tok')
        sync_env['assembly'].assert_called_once_with('tok')

    @pytest.mark.integration
    def test_a_backlogged_queue_skips_the_sync(self, client, sync_env):
        # Enqueuing on top of a 1000-job backlog compounds it; the device is
        # told to try again later instead.
        with patch.object(type(auth_routes.job_queue), 'count',
                          auth_routes.MAX_JOBS + 1):
            response = client.get('/sync_analytics', headers=TOKEN)

        assert 'MAX JOBS EXCEEDED' in response.get_json()
        sync_env['analytics'].assert_not_called()
        sync_env['enqueue'].assert_not_called()

    @pytest.mark.integration
    def test_exactly_at_the_limit_still_syncs(self, client, sync_env):
        with patch.object(type(auth_routes.job_queue), 'count', auth_routes.MAX_JOBS):
            client.get('/sync_analytics', headers=TOKEN)
        sync_env['analytics'].assert_called_once()

    @pytest.mark.integration
    def test_no_access_token_pushes_nothing(self, client, sync_env):
        with patch.object(type(auth_routes.job_queue), 'count', 0):
            response = client.get('/sync_analytics')

        assert response.status_code == 200
        sync_env['analytics'].assert_not_called()
        sync_env['assembly'].assert_not_called()

    @pytest.mark.integration
    def test_no_pending_events_enqueues_nothing(self, client, sync_env):
        with patch.object(type(auth_routes.job_queue), 'count', 0):
            client.get('/sync_analytics', headers=TOKEN)

        sync_env['enqueue'].assert_not_called()
        sync_env['insert'].assert_not_called()

    @pytest.mark.integration
    def test_pending_events_are_queued_for_push(self, client, sync_env):
        sync_env['events'].return_value = {'count': 7, 'records': []}
        with patch.object(type(auth_routes.job_queue), 'count', 0):
            client.get('/sync_analytics', headers=TOKEN)

        args = sync_env['enqueue'].call_args
        assert args[0][0] is auth_routes.push_event_records
        assert args[0][1] == 'https://cloud.example'
        assert args[0][2] == 'tok'
        sync_env['insert'].assert_called_once_with(
            'job-1', 'Pushing 7 events to cloud')

    @pytest.mark.integration
    def test_the_event_push_is_retried(self, client, sync_env):
        sync_env['events'].return_value = {'count': 1}
        with patch.object(type(auth_routes.job_queue), 'count', 0):
            client.get('/sync_analytics', headers=TOKEN)

        kwargs = sync_env['enqueue'].call_args[1]
        assert kwargs['retry'].max == 5
        assert kwargs['job_timeout'] == 1800


class TestSyncFlow:
    @pytest.fixture
    def flow_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path))
        (tmp_path / 'flows.json').write_text('[{"id": "node1"}]')
        return tmp_path

    @pytest.mark.integration
    def test_copies_the_flow_out_of_nodecreator_and_posts_it(self, client, flow_file):
        with patch('os.system') as system, \
             patch.object(auth_routes.utils_db, 'find_one',
                          return_value={'id': 'dev-42'}), \
             patch.object(auth_routes, 'get_cloud_domain',
                          return_value='https://cloud.example'), \
             patch('requests.post',
                   return_value=MagicMock(text='ok', status_code=200)) as post:
            response = client.get('/sync_flow', headers=TOKEN)

        assert response.status_code == 200
        system.assert_called_once_with(
            'docker cp nodecreator:/root/.node-red/flows.json '
            + str(flow_file) + '/flows.json')
        assert post.call_args[0][0] == \
            'https://cloud.example/api/capture/devices/dev-42/flow'
        assert post.call_args[1]['headers']['Authorization'] == 'Bearer tok'

    @pytest.mark.integration
    def test_an_unregistered_device_is_a_404(self, client, flow_file):
        with patch('os.system'), \
             patch.object(auth_routes.utils_db, 'find_one', return_value=None), \
             patch('requests.post') as post:
            response = client.get('/sync_flow', headers=TOKEN)

        assert response.status_code == 404
        post.assert_not_called()

    @pytest.mark.integration
    def test_the_cloud_status_is_passed_through(self, client, flow_file):
        with patch('os.system'), \
             patch.object(auth_routes.utils_db, 'find_one',
                          return_value={'id': 'dev-42'}), \
             patch.object(auth_routes, 'get_cloud_domain',
                          return_value='https://cloud.example'), \
             patch('requests.post',
                   return_value=MagicMock(text='rejected', status_code=422)):
            response = client.get('/sync_flow', headers=TOKEN)

        assert response.status_code == 422
        assert response.get_json() == 'rejected'


class TestRouteRegistration:
    @pytest.mark.integration
    def test_every_path_is_registered(self, client):
        rules = {r.rule for r in client.application.url_map.iter_rules()}
        assert {'/auth_token', '/deauthorize',
                '/sync_analytics', '/sync_flow'} <= rules

    @pytest.mark.integration
    def test_the_job_ceiling_is_a_thousand(self):
        assert auth_routes.MAX_JOBS == 1000
