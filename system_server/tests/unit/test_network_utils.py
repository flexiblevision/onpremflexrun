"""
Unit tests for network utility functions
"""
import pytest
from unittest.mock import Mock, patch, MagicMock, mock_open
from utils.network_utils import (
    is_valid_ip,
    get_eth_port_names,
    get_static_ip_ref,
    get_interface_name_ref,
)


class TestIPValidation:
    """Tests for IP address validation"""

    @pytest.mark.unit
    def test_is_valid_ip_valid(self):
        """Test validation of valid IP addresses"""
        assert is_valid_ip('192.168.1.1') is True
        assert is_valid_ip('10.0.0.1') is True
        assert is_valid_ip('172.16.0.1') is True
        assert is_valid_ip('255.255.255.255') is True
        assert is_valid_ip('0.0.0.0') is True

    @pytest.mark.unit
    def test_is_valid_ip_invalid(self):
        """Test validation of invalid IP addresses"""
        assert is_valid_ip('256.1.1.1') is False
        assert is_valid_ip('192.168.1') is False
        assert is_valid_ip('192.168.1.1.1') is False
        assert is_valid_ip('abc.def.ghi.jkl') is False
        assert is_valid_ip('') is False
        assert is_valid_ip(None) is False
        assert is_valid_ip('192.168.-1.1') is False

    @pytest.mark.unit
    def test_is_valid_ip_edge_cases(self):
        """Test edge cases for IP validation"""
        assert is_valid_ip('192.168.001.001') is True
        assert is_valid_ip('1.1.1.1') is True


class TestEthPortNames:
    """Tests for ethernet port name detection"""

    @pytest.mark.unit
    @patch('os.popen')
    def test_get_eth_port_names_multiple_ports(self, mock_popen):
        """Test getting multiple ethernet port names"""
        mock_popen.return_value.read.return_value = 'lo\nenp0s31f6\nenp3s0\neth0\nwlan0'

        result = get_eth_port_names()

        assert 'enp0s31f6' in result
        assert 'enp3s0' in result
        assert 'eth0' in result
        assert 'wlan0' not in result
        assert 'lo' not in result
        assert len(result) == 3

    @pytest.mark.unit
    @patch('os.popen')
    def test_get_eth_port_names_single_port(self, mock_popen):
        """Test getting single ethernet port name"""
        mock_popen.return_value.read.return_value = 'lo\nenp0s31f6\nwlan0'

        result = get_eth_port_names()

        assert 'enp0s31f6' in result
        assert len(result) == 1

    @pytest.mark.unit
    @patch('os.popen')
    def test_get_eth_port_names_no_ports(self, mock_popen):
        """Test when no ethernet ports found"""
        mock_popen.return_value.read.return_value = 'lo\nwlan0'

        result = get_eth_port_names()

        assert len(result) == 0

    @pytest.mark.unit
    @patch('os.popen')
    def test_get_eth_port_names_sorted(self, mock_popen):
        """Test that port names are returned sorted"""
        mock_popen.return_value.read.return_value = 'eth2\neth0\neth1\nenp0s1'

        result = get_eth_port_names()

        assert result == sorted(result)


class TestStaticIPConfiguration:
    """Tests for static IP configuration"""

    @pytest.mark.unit
    @patch('settings.config', {'static_ip': '192.168.10.50'})
    def test_get_static_ip_ref_from_config(self):
        """Test getting static IP from configuration"""
        result = get_static_ip_ref()
        assert result == '192.168.10.50'

    @pytest.mark.unit
    @patch('settings.config', {})
    def test_get_static_ip_ref_default(self):
        """Test getting default static IP"""
        result = get_static_ip_ref()
        assert result == '192.168.10.35'


class TestInterfaceNameRef:
    """Tests for interface name reference"""

    @pytest.mark.unit
    @patch('utils.network_utils.get_eth_port_names')
    def test_get_interface_name_ref_single_port(self, mock_get_ports):
        """Test getting interface name with single port"""
        mock_get_ports.return_value = ['enp0s31f6']

        result = get_interface_name_ref()

        assert result == 'enp0s31f6'

    @pytest.mark.unit
    @patch('utils.network_utils.get_eth_port_names')
    def test_get_interface_name_ref_multiple_ports(self, mock_get_ports):
        """Test getting interface name with multiple ports"""
        mock_get_ports.return_value = ['enp0s31f6', 'enp3s0', 'eth0']

        result = get_interface_name_ref()

        assert result == 'eth0'  # Should return last port

    @pytest.mark.unit
    @patch('utils.network_utils.get_eth_port_names')
    def test_get_interface_name_ref_no_ports(self, mock_get_ports):
        """Test getting interface name with no ports"""
        mock_get_ports.return_value = []

        result = get_interface_name_ref()

        assert result == 'enp0s31f6'  # Default


class TestNetplanOperations:
    """Tests for netplan configuration operations"""

    @pytest.mark.unit
    @patch('os.system')
    @patch('os.path.exists', return_value=True)
    @patch('builtins.open', new_callable=mock_open)
    @patch('utils.network_utils.get_interface_name_ref')
    def test_set_static_ips(self, mock_get_interface, mock_file, mock_exists, mock_system):
        """Test setting static IP configuration"""
        from utils.network_utils import set_static_ips

        mock_get_interface.return_value = 'enp0s31f6'

        set_static_ips('192.168.1.100')

        # Verify file was opened for writing
        mock_file.assert_called_once_with('/etc/netplan/fv-net-init.yaml', 'w')

        # Verify netplan apply was called
        mock_system.assert_called_once_with('sudo netplan apply')

        # Verify correct content was written
        handle = mock_file()
        write_calls = [call[0][0] for call in handle.write.call_args_list]
        assert any('192.168.1.100/24' in call for call in write_calls)
        assert any('enp0s31f6' in call for call in write_calls)

    @pytest.mark.unit
    @patch('os.system')
    @patch('os.path.exists', return_value=True)
    @patch('builtins.open', new_callable=mock_open)
    @patch('utils.network_utils.get_interface_name_ref')
    def test_set_static_ips_invalid(self, mock_get_interface, mock_file, mock_exists, mock_system):
        """Test setting static IP with invalid IP"""
        from utils.network_utils import set_static_ips

        mock_get_interface.return_value = 'enp0s31f6'

        set_static_ips('invalid_ip')

        # Should still write config but without the invalid IP
        mock_file.assert_called_once()
        handle = mock_file()
        write_calls = [call[0][0] for call in handle.write.call_args_list]
        assert not any('invalid_ip' in call for call in write_calls)


class TestStoreNetplanSettings:
    """Tests for storing netplan settings"""

    @pytest.mark.unit
    @patch('utils.network_utils.interfaces_db')
    @patch('datetime.datetime')
    def test_store_netplan_settings_success(self, mock_datetime, mock_db):
        """Test successfully storing netplan settings"""
        from utils.network_utils import store_netplan_settings

        mock_datetime.now.return_value = '2024-01-01 00:00:00'

        config = {
            'lanPort': 'enp0s31f6',
            'ip': '192.168.1.100',
            'dhcp': False
        }

        store_netplan_settings(config)

        mock_db.update_one.assert_called_once()

    @pytest.mark.unit
    @patch('pymongo.MongoClient')
    def test_store_netplan_settings_invalid_ip(self, mock_mongo):
        """Test storing netplan settings with invalid IP"""
        from utils.network_utils import store_netplan_settings

        mock_collection = MagicMock()
        mock_mongo.return_value.__getitem__.return_value.__getitem__.return_value = mock_collection

        config = {
            'lanPort': 'enp0s31f6',
            'ip': 'invalid_ip',
            'dhcp': False
        }

        # Should handle error gracefully
        store_netplan_settings(config)

        # Should not call update_one with invalid IP
        mock_collection.update_one.assert_not_called()


class TestGetLanIps:
    """Tests for getting LAN IP addresses"""

    @pytest.mark.unit
    @patch('subprocess.Popen')
    @patch('utils.network_utils.get_eth_port_names')
    @patch('pymongo.MongoClient')
    def test_get_lan_ips_single_interface(self, mock_mongo, mock_get_ports, mock_popen):
        """Test getting LAN IPs for single interface"""
        from utils.network_utils import get_lan_ips

        mock_get_ports.return_value = ['enp0s31f6']

        # Mock ifconfig output
        mock_process = MagicMock()
        mock_process.communicate.return_value = (
            b'enp0s31f6: flags=4163<UP,BROADCAST,RUNNING,MULTICAST>  mtu 1500\n'
            b'        inet 192.168.1.100  netmask 255.255.255.0  broadcast 192.168.1.255\n',
            b''
        )
        mock_popen.return_value = mock_process

        # Mock database
        mock_collection = MagicMock()
        mock_collection.find_one.return_value = None
        mock_mongo.return_value.__getitem__.return_value.__getitem__.return_value = mock_collection

        result = get_lan_ips()

        assert len(result) == 1
        assert result[0]['port'] == 'enp0s31f6'
        assert result[0]['name'] == 'LAN1'
        assert result[0]['ip'] == '192.168.1.100'

    @pytest.mark.unit
    @patch('subprocess.Popen')
    @patch('utils.network_utils.get_eth_port_names')
    @patch('pymongo.MongoClient')
    def test_get_lan_ips_no_ip_assigned(self, mock_mongo, mock_get_ports, mock_popen):
        """Test getting LAN IPs when no IP is assigned"""
        from utils.network_utils import get_lan_ips

        mock_get_ports.return_value = ['enp0s31f6']

        # Mock ifconfig output with no inet
        mock_process = MagicMock()
        mock_process.communicate.return_value = (
            b'enp0s31f6: flags=4163<UP,BROADCAST,RUNNING,MULTICAST>  mtu 1500\n',
            b''
        )
        mock_popen.return_value = mock_process

        # Mock database
        mock_collection = MagicMock()
        mock_collection.find_one.return_value = None
        mock_mongo.return_value.__getitem__.return_value.__getitem__.return_value = mock_collection

        result = get_lan_ips()

        assert len(result) == 1
        assert result[0]['ip'] == 'LAN IP not assigned'
