"""
Unit tests for device utility functions
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from utils.device_utils import (
    get_mac_id,
    system_info,
    system_arch,
    list_usb_paths,
    base_path
)


class TestMacId:
    """Tests for MAC ID retrieval"""

    @pytest.mark.unit
    @patch('subprocess.Popen')
    def test_get_mac_id_wireless(self, mock_popen):
        """Test getting MAC ID from wireless interface"""
        # Mock ifconfig call
        ifconfig_process = MagicMock()
        ifconfig_process.communicate.return_value = (
            b'wlp2s0: flags=4163<UP,BROADCAST>  mtu 1500\n'
            b'        ether aa:bb:cc:dd:ee:ff  txqueuelen 1000\n',
            b''
        )

        # Mock cat call for MAC address
        cat_process = MagicMock()
        cat_process.communicate.return_value = (b'aa:bb:cc:dd:ee:ff\n', b'')

        mock_popen.side_effect = [ifconfig_process, cat_process]

        result = get_mac_id()

        assert result == 'aa:bb:cc:dd:ee:ff'
        assert mock_popen.call_count == 2

    @pytest.mark.unit
    @patch('subprocess.Popen')
    def test_get_mac_id_ethernet(self, mock_popen):
        """Test getting MAC ID from ethernet interface"""
        # Mock ifconfig call
        ifconfig_process = MagicMock()
        ifconfig_process.communicate.return_value = (
            b'enp0s31f6: flags=4163<UP,BROADCAST>  mtu 1500\n'
            b'        ether 11:22:33:44:55:66  txqueuelen 1000\n',
            b''
        )

        # Mock cat call for MAC address
        cat_process = MagicMock()
        cat_process.communicate.return_value = (b'11:22:33:44:55:66\n', b'')

        mock_popen.side_effect = [ifconfig_process, cat_process]

        result = get_mac_id()

        assert result == '11:22:33:44:55:66'

    @pytest.mark.unit
    @patch('subprocess.Popen')
    def test_get_mac_id_no_interface(self, mock_popen):
        """Test getting MAC ID when no valid interface found"""
        # Mock ifconfig with no wl or enp interfaces
        ifconfig_process = MagicMock()
        ifconfig_process.communicate.return_value = (
            b'lo: flags=73<UP,LOOPBACK,RUNNING>  mtu 65536\n',
            b''
        )

        mock_popen.return_value = ifconfig_process

        result = get_mac_id()

        assert result is None


class TestSystemInfo:
    """Tests for system information retrieval"""

    @pytest.mark.unit
    @patch('subprocess.Popen')
    def test_system_info_success(self, mock_popen):
        """Test successful system info retrieval"""
        # Mock lshw output
        lshw_process = MagicMock()
        lshw_process.stdout = MagicMock()

        # Mock grep output
        grep_process = MagicMock()
        grep_process.communicate.return_value = (
            b'H/W path           Device      Class          Description\n'
            b'                               system         Computer Name',
            b''
        )

        mock_popen.side_effect = [lshw_process, grep_process]

        result = system_info()

        assert 'system' in result
        assert 'Computer Name' in result

    @pytest.mark.unit
    @patch('subprocess.Popen')
    def test_system_info_empty(self, mock_popen):
        """Test system info when no output"""
        lshw_process = MagicMock()
        lshw_process.stdout = MagicMock()

        grep_process = MagicMock()
        grep_process.communicate.return_value = (b'', b'')

        mock_popen.side_effect = [lshw_process, grep_process]

        result = system_info()

        assert result == ''


class TestSystemArch:
    """Tests for system architecture retrieval"""

    @pytest.mark.unit
    @patch('subprocess.Popen')
    def test_system_arch_x86_64(self, mock_popen):
        """Test getting x86_64 architecture"""
        mock_process = MagicMock()
        mock_process.communicate.return_value = (b'x86_64\n', b'')
        mock_popen.return_value = mock_process

        result = system_arch()

        assert result == 'x86_64'

    @pytest.mark.unit
    @patch('subprocess.Popen')
    def test_system_arch_aarch64(self, mock_popen):
        """Test getting aarch64 architecture"""
        mock_process = MagicMock()
        mock_process.communicate.return_value = (b'aarch64\n', b'')
        mock_popen.return_value = mock_process

        result = system_arch()

        assert result == 'aarch64'


class TestListUsbPaths:
    """Tests for USB path listing"""

    @pytest.mark.unit
    @patch('subprocess.Popen')
    def test_list_usb_paths_vfat(self, mock_popen):
        """Test listing USB paths with vfat filesystem"""
        mock_process = MagicMock()
        mock_process.communicate.return_value = (
            b'/dev/sdb1\n/dev/sdc1\n',
            b''
        )
        mock_popen.return_value = mock_process

        result = list_usb_paths()

        assert len(result) >= 1
        assert 'sdc1' in result[-1]

    @pytest.mark.unit
    @patch('subprocess.Popen')
    def test_list_usb_paths_multiple_formats(self, mock_popen):
        """Test listing USB paths with multiple filesystem formats"""
        # First call for vfat
        vfat_process = MagicMock()
        vfat_process.communicate.return_value = (b'/dev/sdb1\n', b'')

        # Second call for exfat
        exfat_process = MagicMock()
        exfat_process.communicate.return_value = (b'/dev/sdc1\n', b'')

        mock_popen.side_effect = [vfat_process, exfat_process]

        result = list_usb_paths()

        assert len(result) == 2
        assert 'sdb1' in result
        assert 'sdc1' in result

    @pytest.mark.unit
    @patch('subprocess.Popen')
    def test_list_usb_paths_no_devices(self, mock_popen):
        """Test listing USB paths with no devices"""
        mock_process = MagicMock()
        mock_process.communicate.return_value = (b'', b'')
        mock_popen.return_value = mock_process

        result = list_usb_paths()

        assert len(result) == 0


class TestBasePath:
    """Tests for base path determination"""

    @pytest.mark.unit
    @patch('os.path.exists')
    def test_base_path_xavier_ssd_exists(self, mock_exists):
        """Test base path when xavier_ssd exists"""
        mock_exists.return_value = True

        result = base_path()

        assert result == '/xavier_ssd/'

    @pytest.mark.unit
    @patch('os.path.exists')
    def test_base_path_xavier_ssd_not_exists(self, mock_exists):
        """Test base path when xavier_ssd doesn't exist"""
        mock_exists.return_value = False

        result = base_path()

        assert result == '/'


class TestEdgeCases:
    """Tests for edge cases and error handling"""

    @pytest.mark.unit
    @patch('subprocess.Popen')
    def test_get_mac_id_with_error(self, mock_popen):
        """Test MAC ID retrieval with subprocess error"""
        mock_popen.side_effect = Exception('Command failed')

        with pytest.raises(Exception):
            get_mac_id()

    @pytest.mark.unit
    @patch('subprocess.Popen')
    def test_system_info_with_error(self, mock_popen):
        """Test system info retrieval with error"""
        mock_popen.side_effect = Exception('Command failed')

        with pytest.raises(Exception):
            system_info()
