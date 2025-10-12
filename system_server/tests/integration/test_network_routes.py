"""
Integration tests for network configuration routes
"""
import pytest
import json
from unittest.mock import patch, MagicMock


@pytest.fixture
def app_with_network_routes():
    """Create app with network routes registered"""
    from flask import Flask, jsonify
    from flask_restx import Api
    import auth

    app = Flask(__name__)
    app.config['TESTING'] = True
    api = Api(app)

    # Register auth error handler
    @app.errorhandler(auth.AuthError)
    def handle_auth_error(ex):
        response = jsonify(ex.error)
        response.status_code = ex.status_code
        return response

    # Use mock config with necessary keys for job_manager
    mock_config = {
        'latest_stable_ref': 'test_version',
        'use_aws': False
    }

    with patch('settings.config', mock_config):
        from routes import network_routes
        network_routes.register_routes(api)

    return app


@pytest.fixture
def network_client(app_with_network_routes):
    """Create test client"""
    return app_with_network_routes.test_client()


class TestNetworksEndpoint:
    """Tests for networks listing endpoint"""

    @pytest.mark.integration
    @patch('subprocess.check_output')
    @patch('subprocess.Popen')
    def test_get_networks_success(self, mock_popen, mock_check_output, network_client):
        """Test successful network listing"""
        mock_check_output.return_value = b'SSID\nWiFi-Network-1\nWiFi-Network-2\n'

        # Mock ifconfig for IP
        mock_process = MagicMock()
        mock_process.communicate.return_value = (
            b'wlp2s0: inet 192.168.1.100 netmask 255.255.255.0\n',
            b''
        )
        mock_popen.return_value = mock_process

        response = network_client.get('/networks')

        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'ip' in data
        assert '192.168.1.100' in data['ip']

    @pytest.mark.integration
    @patch('subprocess.check_output')
    @patch('subprocess.Popen')
    def test_get_networks_no_wifi(self, mock_popen, mock_check_output, network_client):
        """Test network listing when not connected to WiFi"""
        mock_check_output.return_value = b'SSID\nWiFi-Network-1\n'

        # Mock ifconfig without inet
        mock_process = MagicMock()
        mock_process.communicate.return_value = (
            b'wlp2s0: flags=4163<UP,BROADCAST>\n',
            b''
        )
        mock_popen.return_value = mock_process

        response = network_client.get('/networks')

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['ip'] == 'Wi-Fi not connected'

    @pytest.mark.integration
    @patch('subprocess.check_output', side_effect=Exception('nmcli not found'))
    @patch('os.system')
    def test_get_networks_nmcli_error(self, mock_system, mock_check_output, network_client):
        """Test handling of nmcli errors"""
        response = network_client.get('/networks')

        # Should restart network manager
        mock_system.assert_called_once()

    @pytest.mark.integration
    @patch('subprocess.check_output')
    @patch('subprocess.Popen')
    @patch('os.system')
    def test_connect_to_network(self, mock_system, mock_popen, mock_check_output, network_client):
        """Test connecting to a WiFi network"""
        mock_system.return_value = 0

        data = {
            'netName': 'TestNetwork',
            'netPassword': 'testpass123'
        }

        response = network_client.post('/networks',
                                       data=json.dumps(data),
                                       content_type='application/json')

        assert response.status_code == 200
        mock_system.assert_called_once()


class TestUpdateIpEndpoint:
    """Tests for IP update endpoint"""

    @pytest.mark.integration
    def test_update_ip_success(self, network_client):
        """Test IP update requires authentication"""
        data = {
            'ip': '192.168.1.100',
            'lanPort': 'enp0s31f6',
            'dhcp': False
        }

        response = network_client.post('/update_ip',
                                       data=json.dumps(data),
                                       content_type='application/json')

        # Should return 401 when auth is missing
        assert response.status_code == 401

    @pytest.mark.integration
    def test_update_ip_invalid_ip(self, network_client):
        """Test IP update with invalid IP requires authentication"""
        data = {
            'ip': 'invalid_ip',
            'lanPort': 'enp0s31f6',
            'dhcp': False
        }

        response = network_client.post('/update_ip',
                                       data=json.dumps(data),
                                       content_type='application/json')

        # Should return 401 when auth is missing
        assert response.status_code == 401

    @pytest.mark.integration
    def test_update_ip_invalid_port(self, network_client):
        """Test IP update with invalid port requires authentication"""
        data = {
            'ip': '192.168.1.100',
            'lanPort': 'invalid_port',
            'dhcp': False
        }

        response = network_client.post('/update_ip',
                                       data=json.dumps(data),
                                       content_type='application/json')

        # Should return 401 when auth is missing
        assert response.status_code == 401


class TestGetLanIpsEndpoint:
    """Tests for LAN IPs retrieval endpoint"""

    @pytest.mark.integration
    @patch('routes.network_routes.get_lan_ips')
    def test_get_lan_ips_success(self, mock_get_lan_ips, network_client):
        """Test successful LAN IP retrieval"""
        mock_get_lan_ips.return_value = [
            {'ip': '192.168.1.100', 'port': 'enp0s31f6', 'name': 'LAN1', 'dhcp': False},
            {'ip': '192.168.2.100', 'port': 'eth0', 'name': 'LAN2', 'dhcp': True}
        ]

        response = network_client.get('/get_lan_ips')

        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data) == 2
        assert data[0]['ip'] == '192.168.1.100'

    @pytest.mark.integration
    @patch('routes.network_routes.get_lan_ips')
    def test_get_lan_ips_no_assignments(self, mock_get_lan_ips, network_client):
        """Test LAN IP retrieval with no assignments"""
        mock_get_lan_ips.return_value = [
            {'ip': 'not assigned', 'port': 'enp0s31f6', 'name': 'LAN1', 'dhcp': False}
        ]

        response = network_client.get('/get_lan_ips')

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data[0]['ip'] == 'not assigned'
