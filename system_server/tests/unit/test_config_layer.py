"""Device configuration: fvconfig.json generation, cloud endpoint resolution
and the DHCP files written to /etc.

fvconfig.json is what every other module reads its endpoints out of, so
generate_environment_config overwriting a commissioned device's config would
repoint it at the wrong cloud. The override guard is the thing under test.

conftest replaces settings and setup.management with mocks for the whole
session; the real modules are loaded here from their files so they are covered
rather than stubbed.
"""
import importlib.util
import json
import os
import subprocess
import sys
import pytest
from unittest.mock import patch, MagicMock, call

import cloud_env


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))


def _load_real(alias, relative_path, extra_modules=None):
    """Load a module from its file, past the conftest stubs in sys.modules."""
    path = os.path.join(REPO, relative_path)
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)

    saved = {}
    for name in (extra_modules or {}):
        saved[name] = sys.modules.get(name)
        sys.modules[name] = extra_modules[name]
    sys.modules[alias] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(alias, None)
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
    return module


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    return tmp_path


# --------------------------------------------------------------------------
# setup/management.py
# --------------------------------------------------------------------------

class TestGenerateEnvironmentConfig:
    def _management(self):
        # Loading the module runs generate_environment_config() at the bottom,
        # which is itself the first assertion: importing settings on a fresh
        # device is what creates fvconfig.json.
        return _load_real('_real_management', 'setup/management.py')

    @pytest.mark.unit
    def test_importing_writes_a_config_on_a_fresh_device(self, home):
        self._management()

        config = json.loads((home / 'fvconfig.json').read_text())
        assert config['environ'] == 'cloud'

    @pytest.mark.unit
    def test_the_cloud_profile_points_at_the_production_endpoints(self, home):
        management = self._management()
        config = json.loads((home / 'fvconfig.json').read_text())

        assert config['cloud_domain'] == 'https://v1.cloud.flexiblevision.com'
        assert config['auth0_domain'] == 'auth.flexiblevision.com'
        assert config['auth_alg'] == 'RS256'
        assert config['use_aws'] is False
        assert config['use_mqtt'] is False

    @pytest.mark.unit
    def test_the_local_profile_points_at_localhost(self, home):
        management = self._management()
        (home / 'fvconfig.json').unlink()

        management.generate_environment_config('local')

        config = json.loads((home / 'fvconfig.json').read_text())
        assert config['environ'] == 'local'
        assert config['cloud_domain'] == 'http://localhost'
        assert config['auth_alg'] == 'HS256'

    @pytest.mark.unit
    def test_an_existing_config_is_not_overwritten(self, home, capsys):
        # A commissioned device's config carries its device id and cloud
        # domain; regenerating it would strand the device.
        management = self._management()
        (home / 'fvconfig.json').write_text('{"environ": "commissioned"}')

        management.generate_environment_config('cloud')

        assert json.loads((home / 'fvconfig.json').read_text()) == \
            {'environ': 'commissioned'}
        assert 'CONFIG EXISTS - DOING NOTHING' in capsys.readouterr().out

    @pytest.mark.unit
    def test_override_replaces_an_existing_config(self, home):
        management = self._management()
        (home / 'fvconfig.json').write_text('{"environ": "commissioned"}')

        management.generate_environment_config('local', True)

        assert json.loads((home / 'fvconfig.json').read_text())['environ'] == 'local'

    @pytest.mark.unit
    def test_a_hotspot_name_is_generated(self, home):
        self._management()

        ssid = json.loads((home / 'fvconfig.json').read_text())['ssid']
        assert ssid.startswith('visioncell_')

    @pytest.mark.unit
    def test_an_unknown_environment_falls_back_to_cloud(self, home):
        management = self._management()
        (home / 'fvconfig.json').unlink()

        management.generate_environment_config('staging')

        assert json.loads((home / 'fvconfig.json').read_text())['environ'] == 'cloud'

    @pytest.mark.unit
    def test_prod_is_the_default_track(self, home):
        """settings.py calls this with no arguments on every import. A device
        must never end up on beta because nobody passed a track."""
        self._management()
        config = json.loads((home / 'fvconfig.json').read_text())

        assert config['release_track'] == 'prod'
        assert config['release_channel'] == 'stable'
        assert config['latest_stable_ref'] == 'latest_stable_version'

    @pytest.mark.unit
    def test_dev_points_at_clouddeploy_and_beta(self, home):
        management = self._management()
        (home / 'fvconfig.json').unlink()

        management.generate_environment_config('cloud', release_track='dev')

        config = json.loads((home / 'fvconfig.json').read_text())
        assert config['cloud_domain'] == 'https://clouddeploy.api.flexiblevision.com'
        assert config['latest_stable_ref'] == 'latest_stable_version_dev'
        assert config['release_channel'] == 'beta'
        assert config['release_track'] == 'dev'

    @pytest.mark.unit
    def test_dev_leaves_the_rest_of_the_profile_alone(self, home):
        management = self._management()
        (home / 'fvconfig.json').unlink()

        management.generate_environment_config('cloud', release_track='dev')

        config = json.loads((home / 'fvconfig.json').read_text())
        assert config['auth0_domain'] == 'auth.flexiblevision.com'
        assert config['environ'] == 'cloud'

    @pytest.mark.unit
    def test_an_unknown_track_is_refused_not_silently_prod(self, home):
        """Silently falling back would put a device meant for testing onto the
        fleet's channel with nothing in the install saying so."""
        management = self._management()
        (home / 'fvconfig.json').unlink()

        with pytest.raises(ValueError):
            management.generate_environment_config('cloud', release_track='beta')

        assert not (home / 'fvconfig.json').exists()

    @pytest.mark.unit
    def test_a_dev_config_does_not_leak_into_the_next_prod_one(self, home):
        """The profiles are module-level dicts. Mutating one in place made the
        next install inherit whatever the previous one chose."""
        management = self._management()
        (home / 'fvconfig.json').unlink()
        management.generate_environment_config('cloud', release_track='dev')
        (home / 'fvconfig.json').unlink()

        management.generate_environment_config('cloud', release_track='prod')

        config = json.loads((home / 'fvconfig.json').read_text())
        assert config['cloud_domain'] == 'https://v1.cloud.flexiblevision.com'
        assert config['latest_stable_ref'] == 'latest_stable_version'
        assert config['release_channel'] == 'stable'

    @pytest.mark.unit
    def test_the_local_profile_takes_a_track_too(self, home):
        management = self._management()
        (home / 'fvconfig.json').unlink()

        management.generate_environment_config('local', release_track='dev')

        config = json.loads((home / 'fvconfig.json').read_text())
        assert config['environ'] == 'local'
        assert config['release_channel'] == 'beta'


class TestManagementUpdateConfig:
    @pytest.mark.unit
    def test_writes_over_an_existing_config(self, home):
        management = _load_real('_real_management', 'setup/management.py')

        management.update_config({'environ': 'local', 'device': 'x'})

        assert json.loads((home / 'fvconfig.json').read_text()) == \
            {'environ': 'local', 'device': 'x'}

    @pytest.mark.unit
    def test_does_nothing_when_there_is_no_config_to_update(self, home):
        management = _load_real('_real_management', 'setup/management.py')
        (home / 'fvconfig.json').unlink()

        management.update_config({'environ': 'local'})

        assert not (home / 'fvconfig.json').exists()


# --------------------------------------------------------------------------
# settings.py
# --------------------------------------------------------------------------

class TestSettingsModule:
    def _settings(self, home, config=None):
        management = _load_real('_real_management', 'setup/management.py')
        if config is not None:
            (home / 'fvconfig.json').write_text(json.dumps(config))
        return _load_real('_real_settings', 'settings.py',
                          extra_modules={'setup.management': management})

    @pytest.mark.unit
    def test_loads_the_config_off_disk(self, home):
        settings = self._settings(home, {'environ': 'cloud', 'use_aws': False})
        assert settings.config['environ'] == 'cloud'

    @pytest.mark.unit
    def test_the_shared_handles_start_empty(self, home):
        # Other modules assign into these; they must exist and be None so a
        # `if settings.FireOperator` check is answerable before startup.
        settings = self._settings(home, {'environ': 'cloud', 'use_aws': False})
        assert settings.kinesis is None
        assert settings.FireOperator is None

    @pytest.mark.unit
    def test_kinesis_is_not_constructed_without_aws(self, home):
        kinesis_module = MagicMock()
        with patch.dict(sys.modules, {'aws.Kinesis': kinesis_module}):
            self._settings(home, {'environ': 'cloud', 'use_aws': False})
        kinesis_module.Kinesis.assert_not_called()

    @pytest.mark.unit
    def test_kinesis_is_constructed_when_aws_is_enabled(self, home):
        kinesis_module = MagicMock()
        with patch.dict(sys.modules, {'aws': MagicMock(),
                                      'aws.Kinesis': kinesis_module}):
            settings = self._settings(home, {'environ': 'cloud', 'use_aws': True})

        kinesis_module.Kinesis.assert_called_once()
        assert settings.kinesis is kinesis_module.Kinesis.return_value


# --------------------------------------------------------------------------
# cloud_env.py
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_cloud_env_cache():
    cloud_env._utils_coll = None
    yield
    cloud_env._utils_coll = None


class TestLocalConfig:
    @pytest.mark.unit
    def test_a_local_config_is_returned(self, home):
        (home / 'fvconfig.json').write_text(
            json.dumps({'environ': 'local', 'cloud_domain': 'http://master'}))

        assert cloud_env._local_config()['cloud_domain'] == 'http://master'

    @pytest.mark.unit
    def test_a_cloud_config_is_not_local(self, home):
        (home / 'fvconfig.json').write_text(json.dumps({'environ': 'cloud'}))
        assert cloud_env._local_config() is None

    @pytest.mark.unit
    def test_a_missing_config_is_not_local(self, home):
        assert cloud_env._local_config() is None

    @pytest.mark.unit
    def test_a_corrupt_config_is_not_local(self, home):
        (home / 'fvconfig.json').write_text('{ not json')
        assert cloud_env._local_config() is None


class TestMasterIpFromDb:
    @pytest.mark.unit
    def test_a_bare_address_gets_an_http_scheme(self):
        coll = MagicMock()
        coll.find_one.return_value = {'config': {'master_ip': '10.0.0.5'}}
        cloud_env._utils_coll = coll

        assert cloud_env._master_ip_from_db() == 'http://10.0.0.5'

    @pytest.mark.unit
    def test_an_address_with_a_scheme_is_left_alone(self):
        coll = MagicMock()
        coll.find_one.return_value = {'config': {'master_ip': 'https://master.local'}}
        cloud_env._utils_coll = coll

        assert cloud_env._master_ip_from_db() == 'https://master.local'

    @pytest.mark.unit
    def test_no_client_mode_record_returns_none(self):
        coll = MagicMock()
        coll.find_one.return_value = None
        cloud_env._utils_coll = coll

        assert cloud_env._master_ip_from_db() is None

    @pytest.mark.unit
    def test_a_record_without_a_master_ip_returns_none(self):
        coll = MagicMock()
        coll.find_one.return_value = {'config': {}}
        cloud_env._utils_coll = coll

        assert cloud_env._master_ip_from_db() is None

    @pytest.mark.unit
    def test_the_collection_handle_is_built_once_and_reused(self):
        client = MagicMock()
        with patch('pymongo.MongoClient', return_value=client) as factory:
            cloud_env._master_ip_from_db()
            cloud_env._master_ip_from_db()

        factory.assert_called_once()

    @pytest.mark.unit
    def test_an_unreachable_mongo_returns_none(self):
        coll = MagicMock()
        coll.find_one.side_effect = Exception('no mongo')
        cloud_env._utils_coll = coll

        assert cloud_env._master_ip_from_db() is None

    @pytest.mark.unit
    def test_the_lookup_is_bounded(self):
        # An unbounded server selection would block every sync request.
        with patch('pymongo.MongoClient', return_value=MagicMock()) as factory:
            cloud_env._master_ip_from_db()

        assert factory.call_args[1]['serverSelectionTimeoutMS'] == 2000


class TestGetCloudDomain:
    @pytest.mark.unit
    def test_a_cloud_device_uses_the_fallback(self, home):
        (home / 'fvconfig.json').write_text(json.dumps({'environ': 'cloud'}))

        assert cloud_env.get_cloud_domain() == cloud_env.DEFAULT_CLOUD_DOMAIN

    @pytest.mark.unit
    def test_a_caller_supplied_fallback_wins_over_the_default(self, home):
        (home / 'fvconfig.json').write_text(json.dumps({'environ': 'cloud'}))

        assert cloud_env.get_cloud_domain('https://custom') == 'https://custom'

    @pytest.mark.unit
    def test_a_local_device_prefers_the_master_recorded_in_mongo(self, home):
        (home / 'fvconfig.json').write_text(
            json.dumps({'environ': 'local', 'cloud_domain': 'http://stale'}))
        coll = MagicMock()
        coll.find_one.return_value = {'config': {'master_ip': '10.0.0.5'}}
        cloud_env._utils_coll = coll

        assert cloud_env.get_cloud_domain() == 'http://10.0.0.5'

    @pytest.mark.unit
    def test_a_local_device_falls_back_to_its_configured_domain(self, home):
        (home / 'fvconfig.json').write_text(
            json.dumps({'environ': 'local', 'cloud_domain': 'http://master'}))
        coll = MagicMock()
        coll.find_one.return_value = None
        cloud_env._utils_coll = coll

        assert cloud_env.get_cloud_domain() == 'http://master'


class TestGetCloudFunctionsBase:
    @pytest.mark.unit
    def test_a_cloud_device_uses_the_functions_proxy(self, home):
        (home / 'fvconfig.json').write_text(json.dumps({'environ': 'cloud'}))

        assert cloud_env.get_cloud_functions_base() == \
            cloud_env.DEFAULT_FUNCTIONS_BASE

    @pytest.mark.unit
    def test_a_local_device_routes_functions_through_the_master(self, home):
        (home / 'fvconfig.json').write_text(
            json.dumps({'environ': 'local', 'cloud_domain': 'http://master'}))
        coll = MagicMock()
        coll.find_one.return_value = None
        cloud_env._utils_coll = coll

        assert cloud_env.get_cloud_functions_base() == \
            'http://master/api/capture/functions/'

    @pytest.mark.unit
    def test_a_trailing_slash_does_not_double_up(self, home):
        (home / 'fvconfig.json').write_text(
            json.dumps({'environ': 'local', 'cloud_domain': 'http://master/'}))
        coll = MagicMock()
        coll.find_one.return_value = None
        cloud_env._utils_coll = coll

        assert cloud_env.get_cloud_functions_base() == \
            'http://master/api/capture/functions/'


# --------------------------------------------------------------------------
# scripts/name_generator.py
# --------------------------------------------------------------------------

class TestDeviceSerial:
    """The identity chain, in the same priority order as serial_number.sh."""

    @pytest.fixture
    def sources(self):
        from scripts import name_generator

        def read(path):
            return files.get(path, '')

        files = {}
        with patch.object(name_generator, '_read', side_effect=read), \
             patch('os.listdir', return_value=['lo', 'eno1']):
            yield files

    @pytest.mark.unit
    def test_board_serial_wins(self, sources):
        from scripts import name_generator
        sources['/sys/class/dmi/id/board_serial'] = 'ABC123'
        sources['/sys/class/dmi/id/product_serial'] = 'XYZ789'

        assert name_generator.device_serial() == 'ABC123'

    @pytest.mark.unit
    def test_product_serial_is_the_second_choice(self, sources):
        from scripts import name_generator
        sources['/sys/class/dmi/id/product_serial'] = 'XYZ789'

        assert name_generator.device_serial() == 'XYZ789'

    @pytest.mark.unit
    def test_cpu_serial_is_the_third_choice(self, sources):
        from scripts import name_generator
        sources['/proc/cpuinfo'] = 'processor\t: 0\nSerial\t\t: 00000000deadbeef\n'

        assert name_generator.device_serial() == '00000000DEADBEEF'

    @pytest.mark.unit
    def test_the_mac_address_is_the_last_resort(self, sources):
        from scripts import name_generator
        sources['/sys/class/net/eno1/address'] = 'a4:f2:c1:00:11:22'

        assert name_generator.device_serial() == 'A4F2C1001122'

    @pytest.mark.unit
    @pytest.mark.parametrize('placeholder',
                             ['NONE', 'NOTSPECIFIED', 'DEFAULTSTRING', 'TOBEFILLEDBYOEM'])
    def test_oem_placeholder_serials_are_rejected(self, sources, placeholder):
        # Every unprogrammed board of a given model ships the same string, so
        # accepting one would give a whole batch the same SSID.
        from scripts import name_generator
        sources['/sys/class/dmi/id/board_serial'] = placeholder
        sources['/sys/class/dmi/id/product_serial'] = 'REAL123'

        assert name_generator.device_serial() == 'REAL123'

    @pytest.mark.unit
    def test_an_all_zero_cpu_serial_is_rejected(self, sources):
        from scripts import name_generator
        sources['/proc/cpuinfo'] = 'Serial\t\t: 0000000000000000\n'
        sources['/sys/class/net/eno1/address'] = 'a4:f2:c1:00:11:22'

        assert name_generator.device_serial() == 'A4F2C1001122'

    @pytest.mark.unit
    def test_the_loopback_interface_is_never_used(self, sources):
        from scripts import name_generator
        sources['/sys/class/net/lo/address'] = '00:00:00:00:00:00'

        assert name_generator.device_serial() == ''

    @pytest.mark.unit
    def test_no_identity_at_all_returns_empty(self, sources):
        from scripts import name_generator
        assert name_generator.device_serial() == ''

    @pytest.mark.unit
    def test_unreadable_paths_are_not_fatal(self):
        from scripts import name_generator
        with patch('builtins.open', side_effect=OSError('permission denied')), \
             patch('os.listdir', side_effect=OSError('no sysfs')):
            assert name_generator.device_serial() == ''


class TestGenerateName:
    @pytest.mark.unit
    def test_the_name_is_prefixed_and_lowercase(self):
        from scripts import name_generator

        with patch.object(name_generator, 'device_serial', return_value='ABC123'):
            name = name_generator.generate_name()

        assert name.startswith('visioncell_')
        assert name == name.lower()

    @pytest.mark.unit
    def test_the_same_device_always_gets_the_same_name(self):
        # This is the point of the scheme: a reimaged device keeps the SSID
        # printed on its chassis.
        from scripts import name_generator

        with patch.object(name_generator, 'device_serial', return_value='ABC123'):
            first = name_generator.generate_name()
            second = name_generator.generate_name()

        assert first == second

    @pytest.mark.unit
    def test_different_devices_get_different_names(self):
        from scripts import name_generator

        names = set()
        for serial in ('ABC123', 'ABC124', 'XYZ789', 'A4F2C1001122'):
            with patch.object(name_generator, 'device_serial', return_value=serial):
                names.add(name_generator.generate_name())

        assert len(names) == 4

    @pytest.mark.unit
    def test_the_raw_serial_is_not_broadcast(self):
        # The SSID goes out over the air; the asset-tag serial should not.
        from scripts import name_generator

        with patch.object(name_generator, 'device_serial', return_value='SN-ABC123'):
            name = name_generator.generate_name()

        assert 'ABC123' not in name.upper()

    @pytest.mark.unit
    def test_the_suffix_is_the_documented_length(self):
        from scripts import name_generator

        with patch.object(name_generator, 'device_serial', return_value='ABC123'):
            suffix = name_generator.generate_name()[len('visioncell_'):]

        assert len(suffix) == name_generator.SUFFIX_LENGTH
        assert all(c in '0123456789abcdef' for c in suffix)

    @pytest.mark.unit
    def test_a_device_with_no_identity_still_gets_a_usable_name(self):
        # First-boot configuration must not fail on a board with no readable
        # serial; such a device gets a working but unstable name.
        from scripts import name_generator

        with patch.object(name_generator, 'device_serial', return_value=''):
            name = name_generator.generate_name()

        suffix = name[len('visioncell_'):]
        assert len(suffix) == name_generator.SUFFIX_LENGTH
        assert all(c in '0123456789abcdef' for c in suffix)

    @pytest.mark.unit
    def test_names_without_an_identity_still_vary(self):
        from scripts import name_generator

        with patch.object(name_generator, 'device_serial', return_value=''):
            names = {name_generator.generate_name() for _ in range(50)}

        assert len(names) > 1

    @pytest.mark.unit
    def test_the_suffix_space_is_large_enough_for_the_fleet(self):
        # 6 hex characters is ~16.7M values: a 50% collision chance needs
        # roughly 4,800 devices, against ~16 for the previous 190-name pool.
        from scripts import name_generator

        assert 16 ** name_generator.SUFFIX_LENGTH > 10_000_000


# --------------------------------------------------------------------------
# helpers/config_helper.py
# --------------------------------------------------------------------------

@pytest.fixture
def config_helper():
    from helpers import config_helper
    return config_helper


class TestWriteSettingsToConfig:
    @pytest.mark.unit
    def test_persists_the_live_settings(self, config_helper, tmp_path, monkeypatch):
        target = tmp_path / 'fvconfig.json'
        monkeypatch.setattr(config_helper, 'PATH', str(target))

        with patch('settings.config', {'environ': 'local', 'cloud_domain': 'http://x'}):
            config_helper.write_settings_to_config()

        assert json.loads(target.read_text()) == \
            {'environ': 'local', 'cloud_domain': 'http://x'}


class TestDhcpFileGeneration:
    INTERFACES = [{'iname': 'enp1s0', 'ip': '192.168.20.1', 'dhcp': True},
                  {'iname': 'enp2s0', 'ip': '192.168.21.1', 'dhcp': True}]

    @pytest.mark.unit
    def test_the_server_defaults_list_every_dhcp_interface(self, config_helper, tmp_path):
        written = {}

        def opener(path, mode):
            handle = MagicMock()
            handle.__enter__.return_value = handle
            handle.write.side_effect = lambda body: written.__setitem__(path, body)
            return handle

        with patch('builtins.open', side_effect=opener):
            config_helper.add_ports_to_env(self.INTERFACES)

        body = written['/etc/default/isc-dhcp-server']
        assert 'INTERFACESv4="enp1s0 enp2s0"' in body
        assert 'INTERFACESv6=""' in body

    @pytest.mark.unit
    def test_the_interfaces_file_brings_each_port_up(self, config_helper):
        written = {}

        def opener(path, mode):
            handle = MagicMock()
            handle.__enter__.return_value = handle
            handle.write.side_effect = lambda body: written.__setitem__(path, body)
            return handle

        with patch('builtins.open', side_effect=opener):
            config_helper.write_interfaces_config(self.INTERFACES)

        body = written['/etc/network/interfaces']
        assert body.startswith('auto lo\niface lo inet loopback\n\n')
        assert 'auto enp1s0\n' in body
        assert 'auto enp2s0\n' in body

    @pytest.mark.unit
    def test_each_port_gets_its_own_subnet_from_its_third_octet(self, config_helper):
        written = {}

        def opener(path, mode):
            handle = MagicMock()
            handle.__enter__.return_value = handle
            handle.write.side_effect = lambda body: written.__setitem__(path, body)
            return handle

        with patch('builtins.open', side_effect=opener):
            config_helper.setup_port_subnets(self.INTERFACES)

        body = written['/etc/dhcp/dhcpd.conf']
        assert 'subnet 192.168.20.0 netmask 255.255.255.0' in body
        assert 'range 192.168.20.50 192.168.20.150;' in body
        assert 'subnet 192.168.21.0 netmask 255.255.255.0' in body
        assert 'authoritative;' in body

    @pytest.mark.unit
    def test_no_interfaces_still_writes_a_valid_header(self, config_helper):
        written = {}

        def opener(path, mode):
            handle = MagicMock()
            handle.__enter__.return_value = handle
            handle.write.side_effect = lambda body: written.__setitem__(path, body)
            return handle

        with patch('builtins.open', side_effect=opener):
            config_helper.setup_port_subnets([])

        assert 'subnet' not in written['/etc/dhcp/dhcpd.conf']


class TestServiceControl:
    @pytest.mark.unit
    def test_restart_invokes_systemctl(self, config_helper):
        with patch('subprocess.check_output', return_value=b'') as run:
            config_helper.restart_service()
        run.assert_called_once_with(
            'systemctl restart isc-dhcp-server.service', shell=True)

    @pytest.mark.unit
    def test_stop_invokes_systemctl(self, config_helper):
        with patch('subprocess.check_output', return_value=b'') as run:
            config_helper.stop_service()
        run.assert_called_once_with(
            'systemctl stop isc-dhcp-server.service', shell=True)


class TestSetDhcp:
    @pytest.mark.unit
    def test_writes_all_three_files_then_restarts(self, config_helper):
        interfaces = [{'iname': 'enp1s0', 'ip': '192.168.20.1'}]
        with patch.object(config_helper.interfaces_db, 'find', return_value=interfaces), \
             patch.object(config_helper, 'add_ports_to_env') as env, \
             patch.object(config_helper, 'write_interfaces_config') as ifaces, \
             patch.object(config_helper, 'setup_port_subnets') as subnets, \
             patch.object(config_helper, 'restart_service') as restart, \
             patch.object(config_helper, 'stop_service') as stop:
            config_helper.set_dhcp()

        env.assert_called_once_with(interfaces)
        ifaces.assert_called_once_with(interfaces)
        subnets.assert_called_once_with(interfaces)
        restart.assert_called_once()
        stop.assert_not_called()

    @pytest.mark.unit
    def test_no_dhcp_interfaces_stops_the_service(self, config_helper):
        # Leaving dhcpd running with no interfaces hands out leases on
        # whatever it last bound to.
        with patch.object(config_helper.interfaces_db, 'find', return_value=[]), \
             patch.object(config_helper, 'add_ports_to_env'), \
             patch.object(config_helper, 'write_interfaces_config'), \
             patch.object(config_helper, 'setup_port_subnets'), \
             patch.object(config_helper, 'restart_service') as restart, \
             patch.object(config_helper, 'stop_service') as stop:
            config_helper.set_dhcp()

        stop.assert_called_once()
        restart.assert_not_called()

    @pytest.mark.unit
    def test_only_interfaces_flagged_for_dhcp_are_selected(self, config_helper):
        with patch.object(config_helper.interfaces_db, 'find', return_value=[]) as find, \
             patch.object(config_helper, 'add_ports_to_env'), \
             patch.object(config_helper, 'write_interfaces_config'), \
             patch.object(config_helper, 'setup_port_subnets'), \
             patch.object(config_helper, 'stop_service'):
            config_helper.set_dhcp()

        find.assert_called_once_with({'dhcp': True})


# --------------------------------------------------------------------------
# scripts/clean_efi.py
# --------------------------------------------------------------------------

class TestCleanEfiScript:
    @pytest.mark.unit
    def test_the_script_does_not_parse(self):
        # `def if __name__ == "__main__":` - a stray `def`. The file has never
        # been loadable, and scripts/system_cleanup.sh runs it under sudo on
        # every cleanup, so that step has always been a no-op that exits
        # non-zero. Left as-is deliberately: correcting the typo would take a
        # privileged path that currently does nothing and make it start moving
        # files under /boot/efi. Pinned so the state is recorded, not assumed.
        import ast

        source = open(os.path.join(REPO, 'scripts/clean_efi.py')).read()
        with pytest.raises(SyntaxError):
            ast.parse(source)

    @pytest.mark.unit
    def test_every_other_committed_module_parses(self):
        # The above is the only one; a second unparseable file should fail here
        # rather than be discovered by a device.
        import ast

        listing = subprocess.check_output(
            ['git', 'ls-files', '*.py'], cwd=REPO).decode().split()
        broken = []
        for relative in listing:
            if relative.startswith('.') or '/._' in relative or relative.startswith('._'):
                continue
            path = os.path.join(REPO, relative)
            # git still lists a deletion that has not been staged yet.
            if not os.path.isfile(path):
                continue
            try:
                ast.parse(open(path).read())
            except SyntaxError:
                broken.append(relative)

        assert broken == ['scripts/clean_efi.py']
