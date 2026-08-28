"""The generic addon surface, and the legacy paths it has to keep answering.

captureui is versioned and upgraded independently of flex-run, so the old
per-addon paths have to behave exactly as they did - body and status code -
against a device that has been upgraded.
"""
import pytest
from unittest.mock import patch, MagicMock

from routes import addon_routes


@pytest.fixture
def client():
    from flask import Flask
    from flask_restx import Api
    app = Flask(__name__)
    app.config['TESTING'] = True
    api = Api(app)
    addon_routes.register_routes(api)
    return app.test_client()


@pytest.fixture(autouse=True)
def isolated():
    """No docker, no mongo, no redis."""
    with patch.object(addon_routes.runtime, 'health', return_value=False), \
         patch.object(addon_routes.state, 'all_records', return_value={}), \
         patch.object(addon_routes.jobs, 'disable_addon') as disable, \
         patch.object(addon_routes.job_queue, 'enqueue',
                      return_value=MagicMock(id='job-1')) as enqueue, \
         patch.object(addon_routes, 'insert_job') as insert:
        yield {'enqueue': enqueue, 'insert': insert, 'disable': disable}


class TestLegacyPaths:
    @pytest.mark.integration
    @pytest.mark.parametrize('path', [
        '/manage_ocr', '/manage_audio_devices', '/manage_assembly_guidance',
        '/ocr_status', '/audio_devices_status', '/assembly_guidance_status'])
    def test_every_old_path_is_still_registered(self, client, path):
        rules = {r.rule for r in client.application.url_map.iter_rules()}
        assert path in rules

    @pytest.mark.integration
    def test_a_path_owned_by_capdev_is_not_claimed(self, client):
        # client_mode lists its path for reference; capdev serves it.
        rules = {r.rule for r in client.application.url_map.iter_rules()}
        assert '/api/capture/features/client_mode' not in rules

    @pytest.mark.integration
    @pytest.mark.parametrize('path,addon', [
        ('/manage_ocr', 'ocr'),
        ('/manage_audio_devices', 'anomaly_audio'),
        ('/manage_assembly_guidance', 'assembly')])
    def test_enabling_queues_the_install(self, client, isolated, path, addon):
        response = client.put(path, json={'state': True})

        assert response.status_code == 200
        assert response.get_json() == 'enabling...'
        assert isolated['enqueue'].call_args[0][0] is addon_routes.jobs.enable_addon
        assert isolated['enqueue'].call_args[0][1] == addon

    @pytest.mark.integration
    def test_the_install_job_is_still_retried(self, client, isolated):
        # A device mid-upgrade fails the first pull; without the retry the
        # addon silently never installs.
        client.put('/manage_audio_devices', json={'state': True})

        assert isolated['enqueue'].call_args[1]['retry'].max == 5
        assert isolated['enqueue'].call_args[1]['job_timeout'] == 900

    @pytest.mark.integration
    @pytest.mark.parametrize('path,addon', [
        ('/manage_ocr', 'ocr'),
        ('/manage_audio_devices', 'anomaly_audio'),
        ('/manage_assembly_guidance', 'assembly')])
    def test_disabling_tears_the_addon_down(self, client, isolated, path, addon):
        response = client.put(path, json={'state': False})

        assert response.status_code == 200
        assert response.get_json() == 'disabled'
        isolated['disable'].assert_called_once_with(addon)
        isolated['enqueue'].assert_not_called()

    @pytest.mark.integration
    def test_a_payload_without_state_is_still_a_404(self, client, isolated):
        response = client.put('/manage_ocr', json={'other': 1})

        assert response.status_code == 404
        assert response.get_json() == 'state key not found'
        isolated['enqueue'].assert_not_called()

    @pytest.mark.integration
    def test_status_is_still_a_bare_boolean(self, client):
        with patch.object(addon_routes.runtime, 'health', return_value=True):
            assert client.get('/ocr_status').get_json() is True
        with patch.object(addon_routes.runtime, 'health', return_value=False):
            assert client.get('/ocr_status').get_json() is False

    @pytest.mark.integration
    def test_each_status_path_probes_its_own_addon(self, client):
        with patch.object(addon_routes.runtime, 'health',
                          return_value=False) as health:
            client.get('/audio_devices_status')
        health.assert_called_once_with('anomaly_audio')


class TestAddonList:
    @pytest.mark.integration
    def test_it_reports_every_addon_for_this_arch(self, client):
        with patch.object(addon_routes.runtime, 'system_arch', return_value='x86'):
            body = client.get('/addons').get_json()

        assert {entry['name'] for entry in body} == {
            'anomaly_audio', 'assembly', 'client_mode', 'ftp', 'ocr', 'timemachine'}

    @pytest.mark.integration
    def test_an_addon_with_no_image_for_this_arch_is_not_offered(self, client):
        with patch.object(addon_routes.runtime, 'system_arch', return_value='arm'):
            body = client.get('/addons').get_json()

        assert 'ocr' not in {entry['name'] for entry in body}

    @pytest.mark.integration
    def test_intent_and_health_are_reported_apart(self, client):
        # An enabled addon whose container has crashed must not read as "off",
        # which is the only thing the old status probe could say.
        records = {'ocr': {'name': 'ocr', 'enabled': True, 'image': 'img'}}
        with patch.object(addon_routes.state, 'all_records', return_value=records), \
             patch.object(addon_routes.runtime, 'health', return_value=False), \
             patch.object(addon_routes.runtime, 'system_arch', return_value='x86'):
            body = client.get('/addons').get_json()

        ocr = next(e for e in body if e['name'] == 'ocr')
        assert ocr['enabled'] is True
        assert ocr['healthy'] is False

    @pytest.mark.integration
    def test_entries_are_ordered_for_the_ui(self, client):
        with patch.object(addon_routes.runtime, 'system_arch', return_value='x86'):
            body = client.get('/addons').get_json()

        orders = [entry['ui'].get('order', 0) for entry in body]
        assert orders == sorted(orders)

    @pytest.mark.integration
    def test_the_anomaly_family_carries_its_group(self, client):
        with patch.object(addon_routes.runtime, 'system_arch', return_value='x86'):
            body = client.get('/addons').get_json()

        audio = next(e for e in body if e['name'] == 'anomaly_audio')
        assert audio['group']['key'] == 'anomaly'

    @pytest.mark.integration
    def test_the_licence_position_is_reported(self, client):
        with patch.object(addon_routes.runtime, 'system_arch', return_value='x86'):
            body = client.get('/addons').get_json()

        ftp = next(e for e in body if e['name'] == 'ftp')
        assert ftp['entitlement']['allowed'] is True
        assert ftp['entitlement']['enforced'] is False


class TestAddonDetail:
    @pytest.mark.integration
    def test_an_unknown_addon_is_a_404(self, client):
        response = client.get('/addons/nope')
        assert response.status_code == 404

    @pytest.mark.integration
    def test_enabling_reports_the_job(self, client, isolated):
        response = client.put('/addons/ocr', json={'state': True})

        assert response.status_code == 200
        assert response.get_json()['job'] == 'job-1'
        assert response.get_json()['status'] == 'enabling'

    @pytest.mark.integration
    def test_a_missing_state_key_is_a_400(self, client, isolated):
        response = client.put('/addons/ocr', json={})

        assert response.status_code == 400
        isolated['enqueue'].assert_not_called()

    @pytest.mark.integration
    def test_an_unlicensed_addon_is_refused_once_enforcement_is_on(
            self, client, isolated, monkeypatch):
        monkeypatch.setattr(addon_routes.entitlements, 'ENFORCED', True)
        response = client.put('/addons/ocr', json={'state': True})

        assert response.status_code == 403
        isolated['enqueue'].assert_not_called()

    @pytest.mark.integration
    def test_nothing_is_refused_while_enforcement_is_off(self, client, isolated):
        assert addon_routes.entitlements.ENFORCED is False
        response = client.put('/addons/ocr', json={'state': True})

        assert response.status_code == 200
