"""Netplan generation and interface discovery.

These functions rewrite /etc/netplan on a device that is reachable only over
the network they configure. The case that matters is remove_conflicting_netplans:
deleting the wrong file takes the machine off the network with no way back
except a site visit, so the wifi guard is asserted directly.
"""
import os
import pytest
from unittest.mock import patch, MagicMock, call, mock_open

from utils import network_utils as nu


class TestRemoveConflictingNetplans:
    @pytest.mark.unit
    def test_a_conflicting_ethernet_config_is_removed(self, tmp_path):
        conflict = tmp_path / '01-network-manager-all.yaml'
        conflict.write_text('network:\n  ethernets:\n    eth0: {}\n')

        with patch.object(nu.os.path, 'exists', return_value=True), \
             patch.object(nu.os, 'listdir', return_value=[conflict.name]), \
             patch.object(nu.os.path, 'join', return_value=str(conflict)), \
             patch.object(nu.os, 'remove') as remove:
            nu.remove_conflicting_netplans()

        remove.assert_called_once_with(str(conflict))

    @pytest.mark.unit
    def test_a_wifi_config_is_preserved(self, tmp_path):
        # Deleting this strands a device whose only link is wifi.
        wifi = tmp_path / '50-wifi.yaml'
        wifi.write_text('network:\n  wifis:\n    wlan0: {}\n')

        with patch.object(nu.os.path, 'exists', return_value=True), \
             patch.object(nu.os, 'listdir', return_value=[wifi.name]), \
             patch.object(nu.os.path, 'join', return_value=str(wifi)), \
             patch.object(nu.os, 'remove') as remove:
            nu.remove_conflicting_netplans()

        remove.assert_not_called()

    @pytest.mark.unit
    def test_the_generated_config_is_never_removed(self):
        with patch.object(nu.os.path, 'exists', return_value=True), \
             patch.object(nu.os, 'listdir', return_value=['fv-net-init.yaml']), \
             patch.object(nu.os, 'remove') as remove:
            nu.remove_conflicting_netplans()

        remove.assert_not_called()

    @pytest.mark.unit
    def test_non_yaml_files_are_left_alone(self):
        with patch.object(nu.os.path, 'exists', return_value=True), \
             patch.object(nu.os, 'listdir', return_value=['README', 'backup.yaml.bak']), \
             patch.object(nu.os, 'remove') as remove:
            nu.remove_conflicting_netplans()

        remove.assert_not_called()

    @pytest.mark.unit
    def test_a_missing_netplan_directory_is_a_no_op(self):
        with patch.object(nu.os.path, 'exists', return_value=False), \
             patch.object(nu.os, 'listdir') as listdir:
            nu.remove_conflicting_netplans()

        listdir.assert_not_called()

    @pytest.mark.unit
    def test_an_unreadable_file_does_not_stop_the_sweep(self, capsys):
        with patch.object(nu.os.path, 'exists', return_value=True), \
             patch.object(nu.os, 'listdir', return_value=['a.yaml', 'b.yaml']), \
             patch('builtins.open', side_effect=PermissionError('denied')), \
             patch.object(nu.os, 'remove') as remove:
            nu.remove_conflicting_netplans()

        remove.assert_not_called()
        assert capsys.readouterr().out.count('Failed to remove') == 2


class TestBuildSetNetplan:
    INTERFACES = [{'iname': 'enp1s0', 'ip_string': '[192.168.20.1/24]'},
                  {'iname': 'enp2s0', 'ip_string': '[192.168.21.1/24]'}]

    def _written(self, opener):
        return ''.join(c.args[0] for c in opener().write.call_args_list)

    @pytest.mark.unit
    def test_writes_one_stanza_per_interface(self):
        opener = mock_open()
        with patch.object(nu.interfaces_db, 'find', return_value=self.INTERFACES), \
             patch.object(nu.os.path, 'exists', return_value=True), \
             patch.object(nu, 'remove_conflicting_netplans'), \
             patch('builtins.open', opener), \
             patch('os.system'):
            nu.build_set_netplan()

        written = self._written(opener)
        assert 'renderer: NetworkManager' in written
        assert '\n    enp1s0:\n' in written
        assert '\n    enp2s0:\n' in written
        assert 'addresses: [192.168.20.1/24]' in written
        assert written.count('mtu: 9000') == 2

    @pytest.mark.unit
    def test_conflicting_configs_are_cleared_first(self):
        # Applying on top of a conflicting config leaves the interface owned by
        # whichever file netplan merges last.
        order = []
        with patch.object(nu.interfaces_db, 'find', return_value=[]), \
             patch.object(nu.os.path, 'exists', return_value=True), \
             patch.object(nu, 'remove_conflicting_netplans',
                          side_effect=lambda: order.append('clear')), \
             patch('builtins.open', mock_open()) as opener, \
             patch('os.system'):
            opener.side_effect = lambda *a, **kw: order.append('write') or mock_open()()
            nu.build_set_netplan()

        assert order[0] == 'clear'

    @pytest.mark.unit
    def test_the_configuration_is_applied(self):
        with patch.object(nu.interfaces_db, 'find', return_value=[]), \
             patch.object(nu.os.path, 'exists', return_value=True), \
             patch.object(nu, 'remove_conflicting_netplans'), \
             patch('builtins.open', mock_open()), \
             patch('os.system') as system:
            nu.build_set_netplan()

        system.assert_called_once_with('sudo netplan apply')

    @pytest.mark.unit
    def test_a_machine_without_netplan_is_left_alone(self, capsys):
        with patch.object(nu.interfaces_db, 'find', return_value=self.INTERFACES), \
             patch.object(nu.os.path, 'exists', return_value=False), \
             patch('builtins.open') as opener, \
             patch('os.system') as system:
            nu.build_set_netplan()

        opener.assert_not_called()
        system.assert_not_called()
        assert 'netplan path does not exist' in capsys.readouterr().out


class TestSetIps:
    @pytest.mark.unit
    def test_stores_builds_and_reconfigures_dhcp(self):
        order = []
        with patch.object(nu, 'store_netplan_settings',
                          side_effect=lambda c: order.append('store')), \
             patch.object(nu, 'build_set_netplan',
                          side_effect=lambda: order.append('build')), \
             patch('helpers.config_helper.set_dhcp',
                   side_effect=lambda: order.append('dhcp')):
            nu.set_ips({'lanPort': 'enp1s0', 'ip': '192.168.20.1', 'dhcp': False})

        assert order == ['store', 'build', 'dhcp']


class TestGetLanIps:
    def _ifconfig(self, output):
        proc = MagicMock()
        proc.communicate.return_value = (output.encode(), b'')
        return proc

    @pytest.mark.unit
    def test_an_assigned_address_is_reported(self):
        output = 'enp1s0: flags=4163\n        inet 192.168.20.1  netmask 255.255.255.0\n'
        with patch.object(nu, 'get_eth_port_names', return_value=['enp1s0']), \
             patch('subprocess.Popen', return_value=self._ifconfig(output)), \
             patch.object(nu.interfaces_db, 'find_one', return_value=None), \
             patch('os.system'):
            lans = nu.get_lan_ips()

        assert lans[0]['ip'] == '192.168.20.1'
        assert lans[0]['port'] == 'enp1s0'
        assert lans[0]['name'] == 'LAN1'

    @pytest.mark.unit
    def test_ports_are_numbered_in_order(self):
        with patch.object(nu, 'get_eth_port_names',
                          return_value=['enp1s0', 'enp2s0']), \
             patch('subprocess.Popen',
                   return_value=self._ifconfig('inet 10.0.0.1  netmask\n')), \
             patch.object(nu.interfaces_db, 'find_one', return_value=None), \
             patch('os.system'):
            lans = nu.get_lan_ips()

        assert [l['name'] for l in lans] == ['LAN1', 'LAN2']

    @pytest.mark.unit
    def test_the_stored_dhcp_flag_is_reported(self):
        output = 'inet 192.168.20.1  netmask 255.255.255.0\n'
        with patch.object(nu, 'get_eth_port_names', return_value=['enp1s0']), \
             patch('subprocess.Popen', return_value=self._ifconfig(output)), \
             patch.object(nu.interfaces_db, 'find_one',
                          return_value={'iname': 'enp1s0', 'dhcp': True}), \
             patch('os.system'):
            assert nu.get_lan_ips()[0]['dhcp'] is True

    @pytest.mark.unit
    def test_dhcp_defaults_to_false_for_an_unknown_port(self):
        with patch.object(nu, 'get_eth_port_names', return_value=['enp1s0']), \
             patch('subprocess.Popen',
                   return_value=self._ifconfig('inet 10.0.0.1  x\n')), \
             patch.object(nu.interfaces_db, 'find_one', return_value=None), \
             patch('os.system'):
            assert nu.get_lan_ips()[0]['dhcp'] is False

    @pytest.mark.unit
    def test_an_unconfigured_port_reports_no_address(self):
        with patch.object(nu, 'get_eth_port_names', return_value=['enp1s0']), \
             patch('subprocess.Popen',
                   return_value=self._ifconfig('enp1s0: flags=4163\n')), \
             patch.object(nu.interfaces_db, 'find_one', return_value=None), \
             patch('os.system'):
            assert nu.get_lan_ips()[0]['ip'] == 'LAN IP not assigned'

    @pytest.mark.unit
    def test_a_link_local_only_port_reports_no_address(self):
        # ifconfig prints inet6 before inet, and the substring test for 'inet'
        # matches the v6 line too. Comparing the two results is what discards
        # a v6-only interface instead of reporting fe80:: as the LAN address.
        output = 'enp1s0: flags=4163\n        inet6 fe80::1  prefixlen 64\n'
        with patch.object(nu, 'get_eth_port_names', return_value=['enp1s0']), \
             patch('subprocess.Popen', return_value=self._ifconfig(output)), \
             patch.object(nu.interfaces_db, 'find_one', return_value=None), \
             patch('os.system'):
            assert nu.get_lan_ips()[0]['ip'] == 'LAN IP not assigned'

    @pytest.mark.unit
    def test_a_new_extra_port_is_auto_assigned_a_subnet(self):
        # Ports past the second are assumed to be added cards and each gets its
        # own /24 so two of them cannot collide.
        ports = ['enp1s0', 'enp2s0', 'enp3s0']
        with patch.object(nu, 'get_eth_port_names', return_value=ports), \
             patch('subprocess.Popen', return_value=self._ifconfig('flags=4163\n')), \
             patch.object(nu.interfaces_db, 'find_one', return_value=None), \
             patch.object(nu, 'set_ips') as set_ips, \
             patch('os.system') as system:
            lans = nu.get_lan_ips()

        assert lans[2]['ip'] == '192.168.8.10'
        set_ips.assert_called_once_with(
            {'ip': '192.168.8.10', 'lanPort': 'enp3s0', 'dhcp': False})
        assert 'sudo ifconfig enp3s0 192.168.8.10' in system.call_args[0][0]

    @pytest.mark.unit
    def test_each_extra_port_gets_a_distinct_subnet(self):
        ports = ['enp1s0', 'enp2s0', 'enp3s0', 'enp4s0']
        with patch.object(nu, 'get_eth_port_names', return_value=ports), \
             patch('subprocess.Popen', return_value=self._ifconfig('flags=4163\n')), \
             patch.object(nu.interfaces_db, 'find_one', return_value=None), \
             patch.object(nu, 'set_ips'), \
             patch('os.system'):
            lans = nu.get_lan_ips()

        assert lans[2]['ip'] == '192.168.8.10'
        assert lans[3]['ip'] == '192.168.9.10'

    @pytest.mark.unit
    def test_an_already_known_port_is_not_reassigned(self):
        ports = ['enp1s0', 'enp2s0', 'enp3s0']
        with patch.object(nu, 'get_eth_port_names', return_value=ports), \
             patch('subprocess.Popen', return_value=self._ifconfig('flags=4163\n')), \
             patch.object(nu.interfaces_db, 'find_one',
                          return_value={'iname': 'enp3s0', 'dhcp': False}), \
             patch.object(nu, 'set_ips') as set_ips, \
             patch('os.system'):
            nu.get_lan_ips()

        set_ips.assert_not_called()

    @pytest.mark.unit
    def test_the_first_two_ports_are_never_auto_assigned(self):
        # LAN1 and LAN2 are the built-in ports and are configured by hand.
        ports = ['enp1s0', 'enp2s0']
        with patch.object(nu, 'get_eth_port_names', return_value=ports), \
             patch('subprocess.Popen', return_value=self._ifconfig('flags=4163\n')), \
             patch.object(nu.interfaces_db, 'find_one', return_value=None), \
             patch.object(nu, 'set_ips') as set_ips, \
             patch('os.system') as system:
            nu.get_lan_ips()

        set_ips.assert_not_called()
        system.assert_not_called()

    @pytest.mark.unit
    def test_no_ethernet_ports_yields_an_empty_list(self):
        with patch.object(nu, 'get_eth_port_names', return_value=[]):
            assert nu.get_lan_ips() == []


class TestIsValidIp:
    @pytest.mark.unit
    @pytest.mark.parametrize('ip', ['192.168.1.1', '0.0.0.0', '255.255.255.255'])
    def test_valid_addresses(self, ip):
        assert nu.is_valid_ip(ip) is True

    @pytest.mark.unit
    @pytest.mark.parametrize('ip', ['192.168.1', '192.168.1.256', 'not-an-ip',
                                    '', None, '192.168.1.1.1'])
    def test_invalid_addresses(self, ip):
        assert nu.is_valid_ip(ip) is False


class TestGetInterfaceNameRef:
    @pytest.mark.unit
    def test_a_single_port_machine_uses_the_documented_default(self):
        with patch.object(nu, 'get_eth_port_names', return_value=['enp0s31f6']):
            assert nu.get_interface_name_ref() == 'enp0s31f6'

    @pytest.mark.unit
    def test_no_ports_at_all_uses_the_documented_default(self):
        with patch.object(nu, 'get_eth_port_names', return_value=[]):
            assert nu.get_interface_name_ref() == 'enp0s31f6'

    @pytest.mark.unit
    def test_a_multi_port_machine_uses_the_last_port(self):
        with patch.object(nu, 'get_eth_port_names',
                          return_value=['enp1s0', 'enp2s0']):
            assert nu.get_interface_name_ref() == 'enp2s0'


class TestGetStaticIpRef:
    @pytest.mark.unit
    def test_the_configured_address_is_used(self):
        with patch('settings.config', {'static_ip': '10.0.0.5'}):
            assert nu.get_static_ip_ref() == '10.0.0.5'

    @pytest.mark.unit
    def test_the_documented_default_is_used_when_unset(self):
        with patch('settings.config', {}):
            assert nu.get_static_ip_ref() == '192.168.10.35'


class TestGetEthPortNames:
    @pytest.mark.unit
    def test_only_ethernet_interfaces_are_returned(self):
        with patch('os.popen') as popen:
            popen.return_value.read.return_value = 'lo\nenp2s0\nwlan0\neth0\ndocker0\n'
            assert nu.get_eth_port_names() == ['enp2s0', 'eth0']

    @pytest.mark.unit
    def test_the_order_is_stable(self):
        # get_interface_name_ref indexes the last element, so an unstable order
        # would move the configured interface between boots.
        with patch('os.popen') as popen:
            popen.return_value.read.return_value = 'enp3s0\nenp1s0\nenp2s0\n'
            assert nu.get_eth_port_names() == ['enp1s0', 'enp2s0', 'enp3s0']


class TestRestartNetworkManager:
    @pytest.mark.unit
    def test_restarts_the_service(self):
        with patch('os.system') as system:
            nu.restart_network_manager()
        system.assert_called_once_with('service network-manager restart')
