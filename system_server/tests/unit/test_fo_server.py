"""The AWS fire-operator HTTP surface.

/decommission and PUT /aws_warehouse_zone both rewrite fvconfig.json and
repoint the kiosk browser. A decommission that writes the config but leaves the
kiosk on the splash page strands the machine with no way to re-register it.
"""
import importlib.util
import json
import os
import sys
import pytest
from unittest.mock import patch, MagicMock, call


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
AWS_DIR = os.path.join(REPO, 'aws')

DESKTOP_PATH = '/home/visioncell/.config/autostart/launchpad.html.desktop'


def _load_fo_server():
    """fo_server does a bare `from FireOperator import FireOperator`."""
    path = os.path.join(AWS_DIR, 'fo_server.py')
    spec = importlib.util.spec_from_file_location('_fo_server_under_test', path)
    module = importlib.util.module_from_spec(spec)

    sys.path.insert(0, AWS_DIR)
    sys.modules['_fo_server_under_test'] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
        sys.modules.pop('_fo_server_under_test', None)
    return module


fo = _load_fo_server()


@pytest.fixture
def client():
    fo.app.config['TESTING'] = True
    return fo.app.test_client()


@pytest.fixture
def operator():
    """settings.FireOperator, as the routes see it."""
    import settings
    previous = settings.FireOperator
    settings.FireOperator = MagicMock()
    yield settings.FireOperator
    settings.FireOperator = previous


@pytest.fixture
def no_operator():
    import settings
    previous = settings.FireOperator
    settings.FireOperator = None
    yield
    settings.FireOperator = previous


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    return tmp_path


class TestGrpcResolver:
    @pytest.mark.unit
    def test_the_native_dns_resolver_is_selected_at_import(self):
        # The default c-ares resolver fails on these devices' DNS setup, and
        # the override has to be in place before grpc is imported.
        assert os.environ['GRPC_DNS_RESOLVER'] == 'native'


class TestInspectionStatus:
    @pytest.mark.unit
    def test_get_returns_the_operator_status(self, client, operator):
        operator.get_status.return_value = {'state': 'idle'}

        response = client.get('/inspection_status')

        assert response.status_code == 200
        assert response.get_json() == {'state': 'idle'}

    @pytest.mark.unit
    def test_get_without_an_operator_is_a_404(self, client, no_operator):
        response = client.get('/inspection_status')

        assert response.status_code == 404
        assert b'Operator not running' in response.data

    @pytest.mark.unit
    def test_post_forwards_the_update(self, client, operator):
        response = client.post('/inspection_status', json={'state': 'running'})

        assert response.status_code == 200
        operator.update_status.assert_called_once_with({'state': 'running'})

    @pytest.mark.unit
    def test_post_without_an_operator_is_a_404(self, client, no_operator):
        response = client.post('/inspection_status', json={'state': 'running'})
        assert response.status_code == 404


class TestGetZone:
    @pytest.mark.unit
    def test_splits_the_document_key_into_warehouse_and_zone(self, client):
        config = {'fire_operator': {'document': 'WH1_ZONE3'}}
        with patch('settings.config', config):
            assert client.get('/aws_warehouse_zone').get_json() == \
                {'warehouse': 'WH1', 'zone': 'ZONE3'}

    @pytest.mark.unit
    def test_a_decommissioned_device_reports_empty_fields(self, client):
        # '_' is what /decommission writes; it splits into two empty halves.
        with patch('settings.config', {'fire_operator': {'document': '_'}}):
            assert client.get('/aws_warehouse_zone').get_json() == \
                {'warehouse': '', 'zone': ''}

    @pytest.mark.unit
    def test_an_unset_document_reports_empty_fields(self, client):
        with patch('settings.config', {'fire_operator': {'document': ''}}):
            assert client.get('/aws_warehouse_zone').get_json() == \
                {'warehouse': '', 'zone': ''}

    @pytest.mark.unit
    def test_a_malformed_document_key_reports_empty_fields(self, client):
        with patch('settings.config', {'fire_operator': {'document': 'WH1_Z_EXTRA'}}):
            assert client.get('/aws_warehouse_zone').get_json() == \
                {'warehouse': '', 'zone': ''}


class TestUpdateConfig:
    @pytest.mark.unit
    def test_writes_the_config(self, home):
        (home / 'fvconfig.json').write_text('{}')

        fo.update_config({'environ': 'cloud', 'use_aws': True})

        assert json.loads((home / 'fvconfig.json').read_text()) == \
            {'environ': 'cloud', 'use_aws': True}

    @pytest.mark.unit
    def test_does_nothing_when_there_is_no_config(self, home):
        fo.update_config({'environ': 'cloud'})
        assert not (home / 'fvconfig.json').exists()


def _desktop(tmp_path, monkeypatch, lines):
    path = tmp_path / 'launchpad.html.desktop'
    path.write_text(''.join(lines))
    monkeypatch.setattr(os.path, 'expanduser', lambda p: str(path))
    return path


DESKTOP_LINES = ['[Desktop Entry]\n', 'Type=Application\n',
                 'Exec=google-chrome http://localhost:3013/setup &\n',
                 'Name=launchpad\n']


class TestSetLaunchpad:
    @pytest.mark.unit
    def test_points_the_kiosk_at_the_splash_page(self, tmp_path, monkeypatch):
        path = _desktop(tmp_path, monkeypatch, DESKTOP_LINES)

        fo.set_launchpad()

        content = path.read_text()
        assert 'fv_splash.html' in content
        assert 'http://localhost:3013/setup' not in content

    @pytest.mark.unit
    def test_the_other_entries_are_preserved(self, tmp_path, monkeypatch):
        path = _desktop(tmp_path, monkeypatch, DESKTOP_LINES)

        fo.set_launchpad()

        content = path.read_text()
        assert '[Desktop Entry]\n' in content
        assert 'Name=launchpad\n' in content

    @pytest.mark.unit
    def test_the_kiosk_flags_are_set(self, tmp_path, monkeypatch):
        path = _desktop(tmp_path, monkeypatch, DESKTOP_LINES)

        fo.set_launchpad()

        exec_line = [l for l in path.read_text().splitlines()
                     if l.startswith('Exec=')][0]
        assert '-kiosk' in exec_line
        assert '--incognito' in exec_line

    @pytest.mark.unit
    def test_a_missing_desktop_file_is_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(os.path, 'expanduser',
                            lambda p: str(tmp_path / 'absent.desktop'))
        fo.set_launchpad()


class TestEnableSetup:
    @pytest.mark.unit
    def test_points_the_kiosk_at_the_setup_page(self, tmp_path, monkeypatch):
        lines = ['[Desktop Entry]\n', 'Exec=google-chrome file:///splash.html &\n']
        path = _desktop(tmp_path, monkeypatch, lines)

        fo.enable_setup()

        content = path.read_text()
        assert 'http://localhost:3013/setup' in content
        assert 'splash.html' not in content

    @pytest.mark.unit
    def test_a_missing_desktop_file_is_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(os.path, 'expanduser',
                            lambda p: str(tmp_path / 'absent.desktop'))
        fo.enable_setup()


class TestDecommission:
    @pytest.mark.unit
    def test_clears_the_station_and_returns_the_kiosk_to_setup(self, client, home):
        (home / 'fvconfig.json').write_text('{}')
        config = {'fire_operator': {'document': 'WH1_ZONE3'}}

        with patch('settings.config', config), \
             patch.object(fo, 'enable_setup') as setup:
            response = client.get('/decommission')

        assert response.status_code == 200
        assert config['fire_operator']['document'] == '_'
        setup.assert_called_once()

    @pytest.mark.unit
    def test_the_cleared_station_is_persisted(self, client, home):
        (home / 'fvconfig.json').write_text('{}')
        config = {'fire_operator': {'document': 'WH1_ZONE3'}}

        with patch('settings.config', config), patch.object(fo, 'enable_setup'):
            client.get('/decommission')

        written = json.loads((home / 'fvconfig.json').read_text())
        assert written['fire_operator']['document'] == '_'


class TestUpdateZone:
    @pytest.mark.unit
    def test_records_the_station_and_restarts(self, client, home):
        (home / 'fvconfig.json').write_text('{}')
        config = {'fire_operator': {'document': ''}}

        with patch('settings.config', config), \
             patch.object(fo, 'set_launchpad') as launchpad, \
             patch('threading.Timer') as timer:
            response = client.put('/aws_warehouse_zone',
                                  json={'warehouse': 'WH1', 'zone': 'ZONE3'})

        assert response.status_code == 200
        assert config['fire_operator']['document'] == 'WH1_ZONE3'
        launchpad.assert_called_once()

    @pytest.mark.unit
    def test_the_restart_is_deferred_so_the_response_is_delivered(self, client, home):
        # Restarting inline would kill the process before the caller gets its
        # 200 and the UI would report the commissioning as failed.
        (home / 'fvconfig.json').write_text('{}')

        with patch('settings.config', {'fire_operator': {'document': ''}}), \
             patch.object(fo, 'set_launchpad'), \
             patch('threading.Timer') as timer:
            client.put('/aws_warehouse_zone',
                       json={'warehouse': 'WH1', 'zone': 'ZONE3'})

        assert timer.call_args[0][0] == 2.0
        assert timer.call_args[0][1] is fo.restart_server
        timer.return_value.start.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.parametrize('payload', [{'warehouse': 'WH1'}, {'zone': 'Z3'}, {}])
    def test_an_incomplete_payload_changes_nothing(self, client, home, payload):
        config = {'fire_operator': {'document': 'ORIGINAL'}}

        # The guard falls off the end of the function with no return, so Flask
        # cannot build a response. In production that surfaces as a 500 where a
        # 400 belongs: the device is unharmed, but the commissioning UI cannot
        # tell a rejected request from a broken server. Under TESTING the test
        # client re-raises instead of rendering the 500.
        with patch('settings.config', config), \
             patch.object(fo, 'set_launchpad') as launchpad, \
             patch('threading.Timer') as timer:
            with pytest.raises(TypeError, match='did not return a valid response'):
                client.put('/aws_warehouse_zone', json=payload)

        assert config['fire_operator']['document'] == 'ORIGINAL'
        launchpad.assert_not_called()
        timer.assert_not_called()

    @pytest.mark.unit
    def test_a_body_that_is_not_json_is_rejected(self, client, home):
        response = client.put('/aws_warehouse_zone', data='not json',
                              content_type='application/json')

        assert response.status_code >= 400

    @pytest.mark.unit
    def test_the_station_is_persisted(self, client, home):
        (home / 'fvconfig.json').write_text('{}')
        config = {'fire_operator': {'document': ''}}

        with patch('settings.config', config), \
             patch.object(fo, 'set_launchpad'), \
             patch('threading.Timer'):
            client.put('/aws_warehouse_zone',
                       json={'warehouse': 'WH1', 'zone': 'ZONE3'})

        written = json.loads((home / 'fvconfig.json').read_text())
        assert written['fire_operator']['document'] == 'WH1_ZONE3'


class TestRestartServer:
    @pytest.mark.unit
    def test_restarts_itself_through_forever(self, home):
        with patch('os.system') as system:
            fo.restart_server()

        system.assert_called_once_with(
            f'forever restart {home}/flex-run/aws/fo_server.py')


class TestRoutes:
    @pytest.mark.unit
    def test_every_documented_route_is_registered(self, client):
        rules = {(r.rule, tuple(sorted(r.methods - {'HEAD', 'OPTIONS'})))
                 for r in fo.app.url_map.iter_rules()}

        assert ('/inspection_status', ('GET',)) in rules
        assert ('/inspection_status', ('POST',)) in rules
        assert ('/aws_warehouse_zone', ('GET',)) in rules
        assert ('/aws_warehouse_zone', ('PUT',)) in rules
        assert ('/decommission', ('GET',)) in rules

    @pytest.mark.unit
    def test_importing_does_not_start_the_server(self):
        # The bind lives behind __main__; importing must stay inert.
        with patch('flask.Flask.run') as run:
            _load_fo_server()
        run.assert_not_called()
