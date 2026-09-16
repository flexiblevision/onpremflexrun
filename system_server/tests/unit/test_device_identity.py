"""Domain reporting and place caching for the device identity sync."""
import pytest
from unittest.mock import patch, MagicMock

from worker_scripts import device_identity as di


PLACE = {'site_id': 'SITE-1', 'line_id': 'L1', 'station_id': 'ST-03',
         'site_name': 'Fremont', 'line_name': 'Final A', 'station_name': 'Solder'}


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
    def test_inspection_is_never_reported(self):
        # Baseline: every node inspects, so the cloud supplies it, not the device.
        with patch.object(di.addon_state, 'enabled',
                          return_value=['ocr', 'client_mode', 'ftp']):
            assert di.reported_domains() == []

    @pytest.mark.unit
    def test_nothing_enabled_reports_nothing(self):
        with patch.object(di.addon_state, 'enabled', return_value=[]):
            assert di.reported_domains() == []


class TestPushDeviceIdentity:
    @pytest.mark.unit
    def test_reports_domains_and_caches_the_place(self):
        res = MagicMock(status_code=200)
        res.json.return_value = {'place': PLACE, 'domains': ['assembly', 'inspection'],
                                 'unavailable_domains': ['anomaly', 'waveform']}
        with patch.object(di, 'device_id', return_value='FV-I-1'), \
             patch.object(di.addon_state, 'enabled', return_value=['assembly']), \
             patch.object(di.utils_db, 'update_one') as update, \
             patch('requests.put', return_value=res) as put:
            assert di.push_device_identity('https://cloud.example', 'tok') == PLACE

        assert put.call_args[0][0] == \
            'https://cloud.example/api/assembly/stations/device/FV-I-1'
        assert put.call_args[1]['json'] == {'domains': ['assembly']}
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
             patch.object(di.utils_db, 'update_one') as update, \
             patch('requests.put', return_value=res):
            assert di.push_device_identity('https://cloud.example', 'tok') is None

        assert update.call_args[0][1]['$set']['reason'].startswith('device is not')

    @pytest.mark.unit
    def test_an_unreachable_cloud_never_raises(self):
        with patch.object(di, 'device_id', return_value='FV-I-1'), \
             patch.object(di.addon_state, 'enabled', return_value=[]), \
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
