"""
Integration tests for device and hardware routes
"""
import pytest
import json
from unittest.mock import patch, MagicMock


@pytest.fixture
def app_with_device_routes():
    """Create app with device routes registered"""
    from flask import Flask
    from flask_restx import Api

    app = Flask(__name__)
    app.config['TESTING'] = True
    api = Api(app)

    # Patch settings.config with all required keys for testing
    test_config = {
        'ssid': 'test_hotspot',
        'latest_stable_ref': 'test_version',
        'use_aws': False,
        'cloud_domain': 'https://test.flexiblevision.com',
        'static_ip': '192.168.10.35'
    }

    with patch('settings.config', test_config):
        with patch('platform.processor', return_value='x86_64'):
            from routes import device_routes
            device_routes.register_routes(api)

    return app


@pytest.fixture
def device_client(app_with_device_routes):
    """Create test client"""
    return app_with_device_routes.test_client()


class TestMacIdEndpoint:
    """Tests for MAC ID endpoint"""

    @pytest.mark.integration
    @patch('routes.device_routes.get_mac_id')
    def test_get_mac_id_success(self, mock_get_mac, device_client):
        """Test successful MAC ID retrieval"""
        mock_get_mac.return_value = 'aa:bb:cc:dd:ee:ff'

        response = device_client.get('/mac_id')

        assert response.status_code == 200
        assert b'aa:bb:cc:dd:ee:ff' in response.data

    @pytest.mark.integration
    @patch('routes.device_routes.get_mac_id')
    def test_get_mac_id_not_found(self, mock_get_mac, device_client):
        """Test MAC ID when interface not found"""
        mock_get_mac.return_value = None

        response = device_client.get('/mac_id')

        assert response.status_code == 200


class TestDeviceInfoEndpoint:
    """Tests for device info endpoint"""

    @pytest.mark.integration
    @patch('routes.device_routes.subprocess.Popen')
    @patch('routes.device_routes.get_mac_id')
    @patch('routes.device_routes.system_info')
    @patch('routes.device_routes.system_arch')
    @patch('routes.device_routes.get_lan_ips')
    @patch('routes.device_routes.get_system_metrics')
    def test_get_device_info_complete(self, mock_metrics, mock_lan_ips, mock_arch,
                                      mock_sys_info, mock_mac, mock_popen, device_client):
        """Test complete device info retrieval"""
        mock_mac.return_value = 'aa:bb:cc:dd:ee:ff'
        mock_sys_info.return_value = 'Test System'
        mock_arch.return_value = 'x86_64'
        mock_lan_ips.return_value = [
            {'ip': '192.168.1.100', 'port': 'enp0s31f6', 'name': 'LAN1'}
        ]
        mock_metrics.return_value = {'cpu': 50, 'memory': 60}

        # Mock ifconfig calls
        mock_process = MagicMock()
        mock_process.communicate.return_value = (
            b'wlp2s0: inet 192.168.1.50 netmask 255.255.255.0\n',
            b''
        )
        mock_popen.return_value = mock_process

        response = device_client.get('/device_info')

        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'mac_id' in data
        assert 'system' in data
        assert 'arch' in data
        assert 'metrics' in data
        assert data['mac_id'] == 'aa:bb:cc:dd:ee:ff'


class TestCameraUIDEndpoint:
    """Tests for camera UID endpoint"""

    @pytest.mark.integration
    @patch('routes.device_routes.subprocess.Popen')
    def test_get_camera_uid_success(self, mock_popen, device_client):
        """Test successful camera UID retrieval"""
        # Mock udevadm output
        udevadm_process = MagicMock()
        udevadm_process.stdout = MagicMock()

        # Mock grep output
        grep_process = MagicMock()
        grep_process.communicate.return_value = (
            b'ID_VENDOR_ID=046d\n'
            b'ID_MODEL_ID=082d\n'
            b'ID_SERIAL_SHORT=12345678\n',
            b''
        )

        mock_popen.side_effect = [udevadm_process, grep_process]

        response = device_client.get('/camera_uid/0')

        assert response.status_code == 200
        data = response.data.decode('utf-8')
        assert '046d' in data or '082d' in data or '12345678' in data

    @pytest.mark.integration
    @patch('routes.device_routes.subprocess.Popen')
    def test_get_camera_uid_not_found(self, mock_popen, device_client):
        """Test camera UID when device not found"""
        udevadm_process = MagicMock()
        udevadm_process.stdout = MagicMock()

        grep_process = MagicMock()
        grep_process.communicate.return_value = (b'', b'')

        mock_popen.side_effect = [udevadm_process, grep_process]

        response = device_client.get('/camera_uid/99')

        assert response.status_code == 200


class TestGPIOEndpoints:
    """Tests for GPIO pin control endpoints"""

    @pytest.mark.integration
    @pytest.mark.gpio
    @patch('routes.device_routes.toggle_pin')
    def test_toggle_pin_success(self, mock_toggle, device_client):
        """Test successful GPIO pin toggle"""
        mock_toggle.return_value = True

        data = {'pin_num': 7}

        response = device_client.put('/toggle_pin',
                                     data=json.dumps(data),
                                     content_type='application/json')

        assert response.status_code == 200
        assert response.data.strip() == b'true'

    @pytest.mark.integration
    @pytest.mark.gpio
    def test_toggle_pin_no_pin_num(self, device_client):
        """Test toggle pin without pin number"""
        data = {}

        response = device_client.put('/toggle_pin',
                                     data=json.dumps(data),
                                     content_type='application/json')

        assert response.status_code == 200
        assert response.data.strip() == b'false'

    @pytest.mark.integration
    @pytest.mark.gpio
    @patch('routes.device_routes.set_pin_state')
    def test_set_pin_success(self, mock_set_pin, device_client):
        """Test successful pin state setting"""
        mock_set_pin.return_value = True

        data = {'pin_num': 7, 'state': 1}

        response = device_client.put('/set_pin',
                                     data=json.dumps(data),
                                     content_type='application/json')

        assert response.status_code == 200

    @pytest.mark.integration
    @pytest.mark.gpio
    def test_set_pin_missing_params(self, device_client):
        """Test set pin with missing parameters"""
        data = {'pin_num': 7}  # Missing state

        response = device_client.put('/set_pin',
                                     data=json.dumps(data),
                                     content_type='application/json')

        assert response.status_code == 200
        assert response.data.strip() == b'-1'

    @pytest.mark.integration
    @pytest.mark.gpio
    @patch('routes.device_routes.read_pin')
    def test_read_pin_success(self, mock_read_pin, device_client):
        """Test successful pin reading"""
        mock_read_pin.return_value = 1

        data = {'pin_num': 7}

        response = device_client.post('/read_input_pin',
                                      data=json.dumps(data),
                                      content_type='application/json')

        assert response.status_code == 200
        assert response.data.strip() == b'1'

    @pytest.mark.integration
    @pytest.mark.gpio
    def test_read_pin_missing_param(self, device_client):
        """Test read pin without pin number"""
        data = {}

        response = device_client.post('/read_input_pin',
                                      data=json.dumps(data),
                                      content_type='application/json')

        assert response.status_code == 200
        assert response.data.strip() == b'-1'
