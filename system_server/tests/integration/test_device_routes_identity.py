"""Device identity and address reporting on /device_info.

This payload is how the cloud knows which device it is talking to and how to
reach it. The serial number has two sources: the id assigned at authorization,
which is authoritative, and serial_number.sh as a fallback. Preferring the
wrong one would re-register a commissioned device as a new one.
"""
import pytest
from unittest.mock import patch, MagicMock, call

from routes import device_routes as dr


@pytest.fixture
def client():
    from flask import Flask
    from flask_restx import Api
    app = Flask(__name__)
    app.config['TESTING'] = True
    api = Api(app)
    dr.register_routes(api)
    return app.test_client()


def _proc(output=b'', error=None):
    handle = MagicMock()
    if error is not None:
        handle.communicate.side_effect = error
    else:
        handle.communicate.return_value = (output, b'')
    return handle


def popen_dispatch(ifconfig=b'', wifi=None, serial=b'SN-FALLBACK123\n',
                   ifconfig_error=None, wifi_error=None, serial_error=None):
    """A subprocess.Popen double keyed on which command is being run.

    /device_info shells out three times - bare ifconfig, ifconfig <wlan>, and
    serial_number.sh - and the tests need to fail them independently.
    """
    def run(command, *args, **kwargs):
        program = command[0]
        if program.endswith('serial_number.sh'):
            if serial_error:
                raise serial_error
            return _proc(serial)
        if program == 'ifconfig' and len(command) > 1:
            if wifi_error:
                raise wifi_error
            return _proc(wifi if wifi is not None else b'')
        if ifconfig_error:
            raise ifconfig_error
        return _proc(ifconfig)

    return run


# A bare-ifconfig listing that names a wireless interface.
WITH_WIFI = b'eth0: flags=4163\nwlp2s0: flags=4163\n'


@pytest.fixture
def device_environment(tmp_path, monkeypatch):
    """Everything /device_info fans out to."""
    monkeypatch.setenv('HOME', str(tmp_path))

    with patch.object(dr, 'get_lan_ips', return_value=[]), \
         patch.object(dr, 'system_info', return_value='Dell OptiPlex'), \
         patch.object(dr, 'system_arch', return_value='x86_64'), \
         patch.object(dr, 'get_mac_id', return_value='a4:f2:c1:00:11:22'), \
         patch.object(dr, 'get_system_metrics', return_value={'cpu': 5}), \
         patch.object(dr, 'get_presets', return_value=[]), \
         patch.object(dr, 'find_utility', return_value=[]), \
         patch('subprocess.Popen', side_effect=popen_dispatch()), \
         patch('settings.config', {'ssid': 'visioncell_5cd3c6'}):
        yield


def _info(client):
    return client.get('/device_info').get_json()


class TestSerialNumber:
    @pytest.mark.integration
    def test_an_authorized_device_reports_its_assigned_id(self, client, device_environment):
        def utility(kind):
            if kind == 'device_authorized':
                return [{'is_authorized': True}]
            if kind == 'device_id':
                return [{'id': 'dev-42'}]
            return []

        with patch.object(dr, 'find_utility', side_effect=utility):
            assert _info(client)['system_serial_number'] == 'dev-42'

    @pytest.mark.integration
    def test_an_unauthorized_device_falls_back_to_the_script(self, client, device_environment):
        # find_utility returns [] from the fixture.
        assert _info(client)['system_serial_number'] == 'SN-FALLBACK123'

    @pytest.mark.integration
    def test_an_authorized_device_with_no_stored_id_falls_back(self, client, device_environment):
        def utility(kind):
            if kind == 'device_authorized':
                return [{'is_authorized': True}]
            return [{'id': '   '}]

        with patch.object(dr, 'find_utility', side_effect=utility):
            assert _info(client)['system_serial_number'] == 'SN-FALLBACK123'

    @pytest.mark.integration
    def test_a_device_marked_not_authorized_falls_back(self, client, device_environment):
        with patch.object(dr, 'find_utility',
                          return_value=[{'is_authorized': False}]):
            assert _info(client)['system_serial_number'] == 'SN-FALLBACK123'

    @pytest.mark.integration
    def test_an_unreachable_mongo_falls_back(self, client, device_environment, capsys):
        with patch.object(dr, 'find_utility', side_effect=Exception('no mongo')):
            assert _info(client)['system_serial_number'] == 'SN-FALLBACK123'

        assert 'Failed to check authorization' in capsys.readouterr().out

    @pytest.mark.integration
    def test_a_failing_serial_script_leaves_the_field_empty(self, client, device_environment):
        with patch('subprocess.Popen',
                   side_effect=popen_dispatch(serial_error=OSError('no script'))):
            assert _info(client)['system_serial_number'] == ''


class TestLastKnownIp:
    @pytest.mark.integration
    def test_the_wifi_address_is_reported(self, client, device_environment):
        wifi = b'wlp2s0: flags=4163\n        inet 10.0.0.7  netmask 255.255.255.0\n'

        with patch('subprocess.Popen',
                   side_effect=popen_dispatch(ifconfig=WITH_WIFI, wifi=wifi)):
            assert _info(client)['last_known_ip'] == '10.0.0.7'

    @pytest.mark.integration
    def test_lan_addresses_are_prepended(self, client, device_environment):
        wifi = b'wlp2s0: inet 10.0.0.7  netmask\n'
        lans = [{'ip': '192.168.20.1'}, {'ip': '192.168.21.1'}]

        with patch('subprocess.Popen',
                   side_effect=popen_dispatch(ifconfig=WITH_WIFI, wifi=wifi)), \
             patch.object(dr, 'get_lan_ips', return_value=lans):
            reported = _info(client)['last_known_ip']

        assert reported == '192.168.21.1;192.168.20.1;10.0.0.7'

    @pytest.mark.integration
    @pytest.mark.parametrize('placeholder', ['not assigned', 'LAN IP not assigned'])
    def test_unassigned_lan_ports_are_omitted(self, client, device_environment,
                                              placeholder):
        wifi = b'wlp2s0: inet 10.0.0.7  netmask\n'

        with patch('subprocess.Popen',
                   side_effect=popen_dispatch(ifconfig=WITH_WIFI, wifi=wifi)), \
             patch.object(dr, 'get_lan_ips', return_value=[{'ip': placeholder}]):
            assert _info(client)['last_known_ip'] == '10.0.0.7'

    @pytest.mark.integration
    def test_a_machine_with_no_wifi_reports_the_request_host(self, client,
                                                             device_environment):
        # Something has to be reportable, or the device has no address at all
        # in the fleet view. The Host header is what the caller reached it on.
        with patch('subprocess.Popen',
                   side_effect=popen_dispatch(ifconfig=b'eth0: flags=4163\n')):
            assert _info(client)['last_known_ip'] == 'localhost'

    @pytest.mark.integration
    def test_a_failed_wifi_lookup_reports_the_request_host(self, client,
                                                           device_environment):
        import subprocess
        with patch('subprocess.Popen',
                   side_effect=popen_dispatch(
                       ifconfig=WITH_WIFI,
                       wifi_error=subprocess.SubprocessError('boom'))):
            assert _info(client)['last_known_ip'] == 'localhost'

    @pytest.mark.integration
    def test_a_wifi_interface_with_no_address_reports_the_request_host(
            self, client, device_environment):
        with patch('subprocess.Popen',
                   side_effect=popen_dispatch(ifconfig=WITH_WIFI,
                                              wifi=b'wlp2s0: flags=4163\n')):
            assert _info(client)['last_known_ip'] == 'localhost'

    @pytest.mark.integration
    def test_a_failing_ifconfig_is_not_caught(self, client, device_environment):
        # The first ifconfig call sits outside the try, so a machine without
        # net-tools installed fails the whole endpoint rather than degrading.
        import subprocess
        with patch('subprocess.Popen',
                   side_effect=popen_dispatch(
                       ifconfig_error=subprocess.SubprocessError('boom'))):
            with pytest.raises(subprocess.SubprocessError):
                client.get('/device_info')


class TestReportedFields:
    @pytest.mark.integration
    def test_the_hardware_description_is_included(self, client, device_environment):
        info = _info(client)

        assert info['system'] == 'Dell OptiPlex'
        assert info['arch'] == 'x86_64'
        assert info['mac_id'] == 'a4:f2:c1:00:11:22'

    @pytest.mark.integration
    def test_the_hotspot_name_is_included(self, client, device_environment):
        assert _info(client)['hotspot'] == 'visioncell_5cd3c6'

    @pytest.mark.integration
    def test_an_unconfigured_hotspot_is_reported_as_such(self, client, device_environment):
        with patch('settings.config', {}):
            assert _info(client)['hotspot'] == 'not configured'

    @pytest.mark.integration
    def test_metrics_and_presets_are_embedded(self, client, device_environment):
        with patch.object(dr, 'get_presets', return_value=[{'name': 'p1'}]):
            info = _info(client)

        assert info['metrics'] == {'cpu': 5}
        assert info['presets'] == [{'name': 'p1'}]

    @pytest.mark.integration
    def test_a_timestamp_is_included(self, client, device_environment):
        assert _info(client)['last_active']


class TestTogglePinEndpoint:
    @pytest.mark.integration
    def test_a_pin_is_toggled(self, client):
        with patch.object(dr, 'toggle_pin', return_value=True) as toggle:
            response = client.put('/toggle_pin', json={'pin_num': 3})

        assert response.get_json() is True
        toggle.assert_called_once_with(3)

    @pytest.mark.integration
    def test_a_driver_failure_is_reported_as_false(self, client):
        # The caller is a UI button; a 500 with a traceback is not useful there.
        with patch.object(dr, 'toggle_pin', side_effect=OSError('no driver')):
            assert client.put('/toggle_pin', json={'pin_num': 3}).get_json() is False

    @pytest.mark.integration
    def test_a_payload_without_a_pin_is_refused(self, client):
        with patch.object(dr, 'toggle_pin') as toggle:
            assert client.put('/toggle_pin', json={}).get_json() is False
        toggle.assert_not_called()
