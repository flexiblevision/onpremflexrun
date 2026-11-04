"""Domain reporting and place caching for the device identity sync."""
import sys

import pytest
from unittest.mock import patch, MagicMock, mock_open

from worker_scripts import device_identity as di


PLACE = {'site_id': 'SITE-1', 'line_id': 'L1', 'station_id': 'ST-03',
         'site_name': 'Fremont', 'line_name': 'Final A', 'station_name': 'Solder'}
REPORT = {'assembly': {'enabled': True, 'healthy': True, 'last_error': None}}


class TestReportedDomains:
    @pytest.mark.unit
    def test_addons_map_to_domains(self):
        with patch.object(di.addon_state, 'enabled',
                          return_value=['assembly', 'anomaly_visual']):
            assert di.reported_domains() == ['anomaly', 'assembly']

    @pytest.mark.unit
    def test_audio_addon_is_the_waveform_domain(self):
        with patch.object(di.addon_state, 'enabled', return_value=['anomaly_audio']):
            assert di.reported_domains() == ['waveform']

    @pytest.mark.unit
    def test_timemachine_is_the_time_machine_domain(self):
        # The addon name and the domain differ by an underscore. verify_local_install
        # writes the record under the addon name; a device that records clips and
        # reports no time_machine means that record was never written.
        with patch.object(di.addon_state, 'enabled', return_value=['timemachine']):
            assert di.reported_domains() == ['time_machine']

    @pytest.mark.unit
    def test_inspection_is_never_reported(self):
        # Baseline: every node inspects, so the cloud supplies it, not the device.
        with patch.object(di.addon_state, 'enabled',
                          return_value=['ocr', 'client_mode', 'ftp']):
            assert di.reported_domains() == []

    @pytest.mark.unit
    def test_nothing_enabled_reports_nothing(self):
        with patch.object(di.addon_state, 'enabled', return_value=[]):
            assert di.reported_domains() == []


class TestAddonReport:
    def _report(self, records, health):
        with patch.object(di.addon_state, 'all_records', return_value=records), \
             patch.object(di.addon_runtime, 'health', side_effect=health) as probe:
            return di.addon_report(), probe

    @pytest.mark.unit
    def test_an_enabled_addon_that_is_not_answering_says_so(self):
        # The removed-container case: intent says on, the probe says no.
        report, _ = self._report(
            {'anomaly_visual': {'name': 'anomaly_visual', 'enabled': True}},
            lambda name: False)
        assert report['anomaly'] == {'enabled': True, 'healthy': False, 'last_error': None}

    @pytest.mark.unit
    def test_every_domain_addon_is_reported_and_only_enabled_ones_probed(self):
        report, probe = self._report(
            {'assembly': {'enabled': True}, 'timemachine': {'enabled': False,
                                                           'last_error': 'down'}},
            lambda name: True)
        assert sorted(report) == ['anomaly', 'assembly', 'time_machine', 'waveform']
        assert report['assembly']['healthy'] is True
        assert report['time_machine'] == {'enabled': False, 'healthy': None,
                                          'last_error': 'down'}
        assert report['waveform'] == {'enabled': False, 'healthy': None, 'last_error': None}
        probe.assert_called_once_with('assembly')

    @pytest.mark.unit
    def test_a_probe_that_raises_is_unknown_not_down(self):
        def boom(name):
            raise RuntimeError('registry missing')
        report, _ = self._report({'assembly': {'enabled': True}}, boom)
        assert report['assembly']['healthy'] is None


class TestDeviceTimezone:
    """The zone sent is the one on-prem itself presents data in."""

    def _with(self, setting=None, kiosk=None, host=None):
        utils = patch.object(di.utils_db, 'find_one',
                             return_value={'name': setting} if setting else None)
        kiosk_coll = MagicMock()
        kiosk_coll.find_one.return_value = (
            {'cfg': {'localization': {'timezone': kiosk}}} if kiosk else None)
        client = patch.object(di, '_client', {'fvonprem': {'kiosk_site_config': kiosk_coll}})
        opener = patch('builtins.open', mock_open(read_data=host)) if host \
            else patch('builtins.open', side_effect=FileNotFoundError)
        return utils, client, opener

    @pytest.mark.unit
    def test_the_system_setting_wins(self):
        utils, client, opener = self._with('America/Chicago', 'Europe/Berlin', 'UTC')
        with utils, client, opener:
            assert di.device_timezone() == 'America/Chicago'

    @pytest.mark.unit
    def test_then_the_legacy_kiosk_zone_then_the_host(self):
        utils, client, opener = self._with(None, 'Europe/Berlin', 'UTC')
        with utils, client, opener:
            assert di.device_timezone() == 'Europe/Berlin'
        utils, client, opener = self._with(None, None, 'Asia/Tokyo\n')
        with utils, client, opener:
            assert di.device_timezone() == 'Asia/Tokyo'

    @pytest.mark.unit
    def test_an_unknown_zone_is_not_reported(self):
        utils, client, opener = self._with('Mars/Olympus')
        with utils, client, opener:
            assert di.device_timezone() is None

    @pytest.mark.unit
    def test_imports_on_a_python_without_zoneinfo(self, monkeypatch):
        """Python 3.8 devices have no zoneinfo; failing the import took the
        whole system server down with it."""
        import importlib
        monkeypatch.setitem(sys.modules, 'zoneinfo', None)
        try:
            reloaded = importlib.reload(di)
            assert reloaded.ZoneInfo is None
        finally:
            monkeypatch.delitem(sys.modules, 'zoneinfo')
            importlib.reload(di)

    @pytest.mark.unit
    def test_without_zoneinfo_the_system_zone_files_decide(self, tmp_path):
        (tmp_path / 'America').mkdir()
        (tmp_path / 'America' / 'Chicago').write_bytes(b'TZif')
        (tmp_path / 'UTC').write_bytes(b'TZif')
        with patch.object(di, 'ZoneInfo', None), patch.object(di, 'ZONEINFO_DIR', str(tmp_path)):
            assert di.known_zone('America/Chicago')
            assert di.known_zone('UTC')
            assert not di.known_zone('Mars/Olympus')
            assert not di.known_zone('America')
            assert not di.known_zone('../../etc/passwd')
            assert not di.known_zone('/etc/passwd')

    @pytest.mark.unit
    def test_no_zone_omits_the_field(self):
        res = MagicMock(status_code=200)
        res.json.return_value = {'place': PLACE}
        with patch.object(di, 'device_id', return_value='FV-I-1'), \
             patch.object(di.addon_state, 'enabled', return_value=[]), \
             patch.object(di, 'addon_report', return_value={}), \
             patch.object(di, 'device_timezone', return_value=None), \
             patch.object(di.utils_db, 'update_one'), \
             patch('requests.put', return_value=res) as put:
            di.push_device_identity('https://cloud.example', 'tok')
        assert put.call_args[1]['json'] == {'domains': [], 'addons': {}}


class TestPushDeviceIdentity:
    @pytest.mark.unit
    def test_reports_domains_and_caches_the_place(self):
        res = MagicMock(status_code=200)
        res.json.return_value = {'place': PLACE, 'domains': ['assembly', 'inspection'],
                                 'unavailable_domains': ['anomaly', 'waveform']}
        with patch.object(di, 'device_id', return_value='FV-I-1'), \
             patch.object(di.addon_state, 'enabled', return_value=['assembly']), \
             patch.object(di, 'addon_report', return_value=REPORT), \
             patch.object(di, 'device_timezone', return_value='America/Chicago'), \
             patch.object(di.utils_db, 'update_one') as update, \
             patch('requests.put', return_value=res) as put:
            assert di.push_device_identity('https://cloud.example', 'tok') == PLACE

        assert put.call_args[0][0] == \
            'https://cloud.example/api/assembly/stations/device/FV-I-1'
        assert put.call_args[1]['json'] == {'domains': ['assembly'], 'addons': REPORT,
                                            'timezone': 'America/Chicago'}
        cached = update.call_args[0][1]['$set']
        assert cached['place'] == PLACE
        assert cached['unavailable_domains'] == ['anomaly', 'waveform']

    @pytest.mark.unit
    def test_an_unplaced_device_caches_the_reason(self):
        res = MagicMock(status_code=200)
        res.json.return_value = {'place': None,
                                 'reason': 'device is not assigned to a station on a line'}
        with patch.object(di, 'device_id', return_value='FV-I-1'), \
             patch.object(di.addon_state, 'enabled', return_value=[]), \
             patch.object(di, 'addon_report', return_value={}), \
             patch.object(di.utils_db, 'update_one') as update, \
             patch('requests.put', return_value=res):
            assert di.push_device_identity('https://cloud.example', 'tok') is None

        assert update.call_args[0][1]['$set']['reason'].startswith('device is not')

    @pytest.mark.unit
    def test_an_unreachable_cloud_never_raises(self):
        with patch.object(di, 'device_id', return_value='FV-I-1'), \
             patch.object(di.addon_state, 'enabled', return_value=[]), \
             patch.object(di, 'addon_report', return_value={}), \
             patch.object(di.utils_db, 'update_one') as update, \
             patch('requests.put', side_effect=ConnectionError('offline')):
            assert di.push_device_identity('https://cloud.example', 'tok') is None

        # The previous place stands; it is configuration, not telemetry.
        update.assert_not_called()

    @pytest.mark.unit
    def test_an_unregistered_device_does_not_call_out(self):
        with patch.object(di, 'device_id', return_value=None), \
             patch('requests.put') as put:
            assert di.push_device_identity('https://cloud.example', 'tok') is None
        put.assert_not_called()

    @pytest.mark.unit
    def test_a_store_failure_never_breaks_the_sync(self):
        with patch.object(di, 'device_id', side_effect=RuntimeError('mongo down')), \
             patch('requests.put') as put:
            assert di.push_device_identity('https://cloud.example', 'tok') is None
        put.assert_not_called()


class TestCachedPlace:
    @pytest.mark.unit
    def test_returns_the_stored_place(self):
        with patch.object(di.utils_db, 'find_one',
                          return_value={'type': 'device_place', 'place': PLACE}):
            assert di.cached_place() == PLACE

    @pytest.mark.unit
    def test_no_cache_yet_is_none(self):
        with patch.object(di.utils_db, 'find_one', return_value=None):
            assert di.cached_place() is None
