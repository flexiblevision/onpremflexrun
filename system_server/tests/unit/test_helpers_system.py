"""Fleet telemetry: metrics, uptime history and reported software versions.

Everything here is what the cloud sees about a device. get_software_versions in
particular is what a dashboard shows as "running vs latest", so a container it
fails to match reads as up to date when it is not.
"""
import json
import os
import subprocess
import pytest
from unittest.mock import patch, MagicMock, mock_open

from helpers import system as hs


@pytest.fixture(autouse=True)
def clear_metrics_cache():
    """get_system_metrics memoises in module globals; reset around each test."""
    hs._metrics_cache = None
    hs._metrics_cache_time = 0
    yield
    hs._metrics_cache = None
    hs._metrics_cache_time = 0


@pytest.fixture
def no_nvml():
    """Force the nvidia-smi path; the ctypes handle depends on the host."""
    with patch.object(hs, '_nvml', None):
        yield


def _uname(system='Linux', node='visioncell-01', release='5.15.0-84-generic',
           version='#93-Ubuntu', machine='x86_64', processor='x86_64'):
    return MagicMock(system=system, node=node, release=release,
                     version=version, machine=machine, processor=processor)


def _shell_outputs(cpu=b'85\n', memory=b'42.7\n', storage=b'61%\n', gpu=b'17 %\n'):
    """side_effect for the shell=True check_output calls in _collect_system_metrics."""
    def run(cmd, **kwargs):
        command = cmd[0] if isinstance(cmd, list) else cmd
        if 'vmstat' in command:
            return cpu
        if 'free' in command:
            return memory
        if 'df -h' in command:
            return storage
        if 'nvidia-smi' in command:
            return gpu
        raise AssertionError(f'unexpected command: {command}')
    return run


@pytest.fixture
def collected(no_nvml):
    """A fully stubbed _collect_system_metrics environment."""
    with patch('subprocess.check_output', side_effect=_shell_outputs()), \
         patch('platform.uname', return_value=_uname()), \
         patch.object(hs, 'get_service_stats', return_value={}), \
         patch.object(hs, 'get_shutdown_events', return_value=[]), \
         patch.object(hs, 'get_metadata', return_value={}), \
         patch('builtins.open', mock_open(read_data='123456.78 987654.32')):
        yield


class TestCollectSystemMetrics:
    """Parsing the shell one-liners into the metrics payload."""

    @pytest.mark.unit
    def test_cpu_is_reported_as_percent_busy(self, collected):
        # vmstat's column 15 is percent *idle*; the metric is the inverse.
        assert hs._collect_system_metrics()['cpu'] == 15

    @pytest.mark.unit
    def test_memory_percent_is_truncated_to_an_int(self, collected):
        assert hs._collect_system_metrics()['memory'] == 42

    @pytest.mark.unit
    def test_storage_percent_drops_the_sign(self, collected):
        assert hs._collect_system_metrics()['storage'] == 61

    @pytest.mark.unit
    def test_gpu_percent_is_parsed_from_nvidia_smi(self, collected):
        assert hs._collect_system_metrics()['gpu'] == 17

    @pytest.mark.unit
    def test_platform_fields_are_passed_through(self, collected):
        info = hs._collect_system_metrics()
        assert info['system'] == 'Linux'
        assert info['node_name'] == 'visioncell-01'
        assert info['release'] == '5.15.0-84-generic'
        assert info['machine'] == 'x86_64'
        assert info['processor'] == 'x86_64'

    @pytest.mark.unit
    def test_uptime_is_formatted_from_proc_uptime(self, no_nvml):
        # 123456.78s = 1 day, 10 hours, 17 minutes
        with patch('subprocess.check_output', side_effect=_shell_outputs()), \
             patch('platform.uname', return_value=_uname()), \
             patch.object(hs, 'get_service_stats', return_value={}), \
             patch.object(hs, 'get_shutdown_events', return_value=[]), \
             patch.object(hs, 'get_metadata', return_value={}), \
             patch('builtins.open', mock_open(read_data='123456.78 987654.32')):
            info = hs._collect_system_metrics()

        assert info['uptime_seconds'] == 123456
        assert info['uptime'] == '1d 10h 17m'

    @pytest.mark.unit
    @pytest.mark.parametrize('field,bad', [
        ('cpu', {'cpu': b'not-a-number\n'}),
        ('memory', {'memory': b'\n'}),
        ('storage', {'storage': b'oops\n'}),
    ])
    def test_unparseable_output_degrades_to_zero(self, no_nvml, field, bad):
        # A device without vmstat installed must still report the rest.
        with patch('subprocess.check_output', side_effect=_shell_outputs(**bad)), \
             patch('platform.uname', return_value=_uname()), \
             patch.object(hs, 'get_service_stats', return_value={}), \
             patch.object(hs, 'get_shutdown_events', return_value=[]), \
             patch.object(hs, 'get_metadata', return_value={}), \
             patch('builtins.open', mock_open(read_data='1.0 1.0')):
            assert hs._collect_system_metrics()[field] == 0

    @pytest.mark.unit
    def test_failed_command_degrades_to_zero(self, no_nvml):
        with patch('subprocess.check_output',
                   side_effect=subprocess.CalledProcessError(1, 'vmstat')), \
             patch('platform.uname', return_value=_uname()), \
             patch.object(hs, 'get_service_stats', return_value={}), \
             patch.object(hs, 'get_shutdown_events', return_value=[]), \
             patch.object(hs, 'get_metadata', return_value={}), \
             patch('builtins.open', mock_open(read_data='1.0 1.0')):
            info = hs._collect_system_metrics()

        assert info['cpu'] == 0 and info['memory'] == 0
        assert info['storage'] == 0 and info['gpu'] == 0

    @pytest.mark.unit
    def test_machine_without_a_gpu_reports_zero(self, no_nvml):
        with patch('subprocess.check_output',
                   side_effect=_shell_outputs(gpu=b'command not found\n')), \
             patch('platform.uname', return_value=_uname()), \
             patch.object(hs, 'get_service_stats', return_value={}), \
             patch.object(hs, 'get_shutdown_events', return_value=[]), \
             patch.object(hs, 'get_metadata', return_value={}), \
             patch('builtins.open', mock_open(read_data='1.0 1.0')):
            assert hs._collect_system_metrics()['gpu'] == 0

    @pytest.mark.unit
    def test_unreadable_proc_uptime_degrades_to_zero(self, no_nvml):
        with patch('subprocess.check_output', side_effect=_shell_outputs()), \
             patch('platform.uname', return_value=_uname()), \
             patch.object(hs, 'get_service_stats', return_value={}), \
             patch.object(hs, 'get_shutdown_events', return_value=[]), \
             patch.object(hs, 'get_metadata', return_value={}), \
             patch('builtins.open', side_effect=IOError('no /proc')):
            info = hs._collect_system_metrics()

        assert info['uptime_seconds'] == 0
        assert info['uptime'] == '0d 0h 0m'

    @pytest.mark.unit
    def test_nvml_is_preferred_over_shelling_out_to_nvidia_smi(self):
        # Spawning nvidia-smi per poll is the expensive path; when the library
        # is loadable the counter is read in-process.
        nvml = MagicMock()

        def get_rates(handle, ref):
            ref._obj.gpu = 73
            return 0

        nvml.nvmlDeviceGetUtilizationRates.side_effect = get_rates

        with patch.object(hs, '_nvml', nvml), \
             patch('subprocess.check_output', side_effect=_shell_outputs()) as check, \
             patch('platform.uname', return_value=_uname()), \
             patch.object(hs, 'get_service_stats', return_value={}), \
             patch.object(hs, 'get_shutdown_events', return_value=[]), \
             patch.object(hs, 'get_metadata', return_value={}), \
             patch('builtins.open', mock_open(read_data='1.0 1.0')):
            info = hs._collect_system_metrics()

        assert info['gpu'] == 73
        assert not any('nvidia-smi' in str(c) for c in check.call_args_list)

    @pytest.mark.unit
    def test_nvml_failure_degrades_to_zero(self):
        nvml = MagicMock()
        nvml.nvmlDeviceGetHandleByIndex_v2.side_effect = OSError('driver gone')

        with patch.object(hs, '_nvml', nvml), \
             patch('subprocess.check_output', side_effect=_shell_outputs()), \
             patch('platform.uname', return_value=_uname()), \
             patch.object(hs, 'get_service_stats', return_value={}), \
             patch.object(hs, 'get_shutdown_events', return_value=[]), \
             patch.object(hs, 'get_metadata', return_value={}), \
             patch('builtins.open', mock_open(read_data='1.0 1.0')):
            assert hs._collect_system_metrics()['gpu'] == 0

    @pytest.mark.unit
    def test_sub_reports_are_embedded(self, no_nvml):
        with patch('subprocess.check_output', side_effect=_shell_outputs()), \
             patch('platform.uname', return_value=_uname()), \
             patch.object(hs, 'get_service_stats', return_value={'capdev': {}}), \
             patch.object(hs, 'get_shutdown_events', return_value=[{'type': 'reboot'}]), \
             patch.object(hs, 'get_metadata', return_value={'teamviewer_id': '1'}), \
             patch('builtins.open', mock_open(read_data='1.0 1.0')):
            info = hs._collect_system_metrics()

        assert info['services'] == {'capdev': {}}
        assert info['shutdown_events'] == [{'type': 'reboot'}]
        assert info['metadata'] == {'teamviewer_id': '1'}

    @pytest.mark.unit
    def test_save_is_not_performed_unless_asked(self, collected):
        with patch.object(hs, 'save_metrics_to_csv') as save:
            hs._collect_system_metrics(save=False)
        save.assert_not_called()

    @pytest.mark.unit
    def test_save_flag_writes_the_csv(self, collected):
        with patch.object(hs, 'save_metrics_to_csv') as save:
            hs._collect_system_metrics(save=True)
        save.assert_called_once()


class TestGetSystemMetricsCaching:
    """The cache exists to stop concurrent /device requests forking vmstat."""

    @pytest.mark.unit
    def test_first_call_collects(self):
        with patch.object(hs, '_collect_system_metrics', return_value={'cpu': 1}) as collect:
            assert hs.get_system_metrics() == {'cpu': 1}
        collect.assert_called_once_with(False)

    @pytest.mark.unit
    def test_second_call_within_the_ttl_is_served_from_cache(self):
        with patch.object(hs, '_collect_system_metrics', return_value={'cpu': 1}) as collect:
            hs.get_system_metrics()
            hs.get_system_metrics()
        collect.assert_called_once()

    @pytest.mark.unit
    def test_cache_expires_after_the_ttl(self):
        with patch.object(hs, '_collect_system_metrics', return_value={'cpu': 1}) as collect:
            hs.get_system_metrics()
            hs._metrics_cache_time -= (hs.METRICS_CACHE_TTL + 1)
            hs.get_system_metrics()
        assert collect.call_count == 2

    @pytest.mark.unit
    def test_save_always_bypasses_the_cache(self):
        # A save request must write a fresh sample, not re-persist a stale one.
        with patch.object(hs, '_collect_system_metrics', return_value={'cpu': 1}) as collect:
            hs.get_system_metrics()
            hs.get_system_metrics(save=True)
        assert collect.call_count == 2
        assert collect.call_args_list[1][0][0] is True

    @pytest.mark.unit
    def test_concurrent_collection_returns_the_stale_cache(self):
        # Another caller holds the lock: serve what we have rather than queue
        # up a second vmstat. The real lock is held here - it is a plain
        # threading.Lock, so a non-blocking acquire from this thread fails
        # exactly as it would from a competing request thread.
        hs._metrics_cache = {'cpu': 99}
        hs._metrics_cache_time = hs.time.monotonic()

        hs._metrics_cache_lock.acquire()
        try:
            with patch.object(hs, '_collect_system_metrics') as collect:
                assert hs.get_system_metrics(save=True) == {'cpu': 99}
            collect.assert_not_called()
        finally:
            hs._metrics_cache_lock.release()

    @pytest.mark.unit
    @pytest.mark.timeout(10)
    def test_concurrent_collection_with_no_cache_waits_for_the_collector(self):
        # Nothing to serve, so the caller blocks on the collector rather than
        # racing it, then returns whatever landed in the cache.
        import threading

        holding = threading.Event()
        release = threading.Event()

        def holder():
            hs._metrics_cache_lock.acquire()
            holding.set()
            release.wait(5)
            hs._metrics_cache_lock.release()

        thread = threading.Thread(target=holder)
        thread.start()
        assert holding.wait(5)
        threading.Timer(0.05, release.set).start()

        try:
            with patch.object(hs, '_collect_system_metrics') as collect:
                assert hs.get_system_metrics() == {}
            collect.assert_not_called()
        finally:
            release.set()
            thread.join(5)

    @pytest.mark.unit
    def test_lock_is_released_when_collection_raises(self):
        with patch.object(hs, '_collect_system_metrics', side_effect=RuntimeError('boom')):
            with pytest.raises(RuntimeError):
                hs.get_system_metrics()

        # A leaked lock would wedge every later request onto a stale cache.
        assert hs._metrics_cache_lock.acquire(blocking=False) is True
        hs._metrics_cache_lock.release()


class TestGetCurrentBootTime:
    @pytest.mark.unit
    def test_parses_uptime_s_into_epoch_millis(self):
        with patch('subprocess.check_output', return_value=b'2025-08-25 09:14:03\n'):
            ms = hs.get_current_boot_time()

        assert isinstance(ms, int)
        from datetime import datetime
        assert ms == int(datetime(2025, 8, 25, 9, 14, 3).timestamp() * 1000)

    @pytest.mark.unit
    @pytest.mark.parametrize('failure', [
        subprocess.CalledProcessError(1, 'uptime'),
        FileNotFoundError('uptime'),
    ])
    def test_command_failure_returns_none(self, failure):
        with patch('subprocess.check_output', side_effect=failure):
            assert hs.get_current_boot_time() is None

    @pytest.mark.unit
    def test_unparseable_date_returns_none(self):
        with patch('subprocess.check_output', return_value=b'not a date\n'):
            assert hs.get_current_boot_time() is None


LAST_OUTPUT = """\
reboot   system boot  5.15.0-84-generic Mon Aug 25 09:14:03 2025   still running
shutdown system down  5.15.0-84-generic Sun Aug 24 22:10:11 2025 - Mon Aug 25 09:14:03 2025  (11:03)
reboot   system boot  5.15.0-84-generic Sun Aug 24 22:05:00 2025 - Sun Aug 24 22:10:11 2025  (00:05)
reboot   system boot  5.15.0-84-generic Sat Aug 23 08:00:00 2025 - Sun Aug 24 22:05:00 2025  (1+14:05)

wtmp begins Mon Aug 11 09:00:00 2025
"""


class TestGetShutdownEvents:
    """`last -x -F shutdown reboot` parsed into a typed event list."""

    @pytest.mark.unit
    def test_parses_all_event_lines(self):
        with patch('subprocess.check_output', return_value=LAST_OUTPUT.encode()):
            events = hs.get_shutdown_events()

        assert len(events) == 4
        assert all(set(e) == {'type', 'timestamp_ms'} for e in events)

    @pytest.mark.unit
    def test_shutdown_lines_are_shutdowns(self):
        with patch('subprocess.check_output', return_value=LAST_OUTPUT.encode()):
            events = hs.get_shutdown_events()

        assert events[1]['type'] == 'shutdown'

    @pytest.mark.unit
    def test_a_short_boot_is_classified_as_a_reboot(self):
        # 5 minutes of uptime is a reboot cycle, not a session.
        with patch('subprocess.check_output', return_value=LAST_OUTPUT.encode()):
            events = hs.get_shutdown_events()

        assert events[2]['type'] == 'reboot'

    @pytest.mark.unit
    def test_a_long_boot_is_classified_as_a_startup(self):
        with patch('subprocess.check_output', return_value=LAST_OUTPUT.encode()):
            events = hs.get_shutdown_events()

        assert events[0]['type'] == 'startup'   # still running, no duration
        assert events[3]['type'] == 'startup'   # 1 day 14h

    @pytest.mark.unit
    def test_timestamps_are_epoch_millis(self):
        from datetime import datetime
        with patch('subprocess.check_output', return_value=LAST_OUTPUT.encode()):
            events = hs.get_shutdown_events()

        assert events[0]['timestamp_ms'] == \
            int(datetime(2025, 8, 25, 9, 14, 3).timestamp() * 1000)

    @pytest.mark.unit
    def test_wtmp_footer_and_blank_lines_are_skipped(self):
        with patch('subprocess.check_output', return_value=LAST_OUTPUT.encode()):
            assert len(hs.get_shutdown_events()) == 4

    @pytest.mark.unit
    def test_lines_that_are_neither_shutdown_nor_reboot_are_ignored(self):
        out = ('visioncell tty1 Mon Aug 25 09:14:03 2025 - still running\n'
               'reboot   system boot  x Mon Aug 25 09:14:03 2025   still running\n')
        with patch('subprocess.check_output', return_value=out.encode()):
            assert len(hs.get_shutdown_events()) == 1

    @pytest.mark.unit
    def test_lines_without_a_parseable_date_are_ignored(self):
        out = 'reboot   system boot  5.15.0-84-generic  still running\n'
        with patch('subprocess.check_output', return_value=out.encode()):
            assert hs.get_shutdown_events() == []

    @pytest.mark.unit
    def test_limit_caps_the_list(self):
        with patch('subprocess.check_output', return_value=LAST_OUTPUT.encode()):
            assert len(hs.get_shutdown_events(limit=2)) == 2

    @pytest.mark.unit
    def test_a_running_session_with_a_bad_clock_falls_back_to_uptime(self):
        # Devices with a dead RTC boot at the epoch; without this fallback the
        # current session is dropped and the device looks like it never booted.
        out = 'reboot   system boot  5.15.0 Thu Jan 1 00:00:01 1970   still running\n'
        with patch('subprocess.check_output', return_value=out.encode()), \
             patch.object(hs, 'get_current_boot_time', return_value=1724602443000):
            events = hs.get_shutdown_events()

        assert events == [{'type': 'startup', 'timestamp_ms': 1724602443000}]

    @pytest.mark.unit
    def test_bad_clock_with_no_uptime_fallback_drops_the_event(self):
        out = 'reboot   system boot  5.15.0 Thu Jan 1 00:00:01 1970   still running\n'
        with patch('subprocess.check_output', return_value=out.encode()), \
             patch.object(hs, 'get_current_boot_time', return_value=None):
            assert hs.get_shutdown_events() == []

    @pytest.mark.unit
    def test_a_completed_session_with_a_bad_clock_is_dropped(self):
        out = 'shutdown system down  x Thu Jan 1 00:00:01 1970 - Thu Jan 1 01:00:00 1970  (01:00)\n'
        with patch('subprocess.check_output', return_value=out.encode()):
            assert hs.get_shutdown_events() == []

    @pytest.mark.unit
    def test_negative_day_durations_are_treated_as_magnitudes(self):
        # `last` emits (-1+02:00) when the clock jumped backwards mid-session.
        out = 'reboot system boot x Mon Aug 25 09:14:03 2025 - Mon Aug 25 11:14:03 2025  (-1+02:00)\n'
        with patch('subprocess.check_output', return_value=out.encode()):
            assert hs.get_shutdown_events()[0]['type'] == 'startup'

    @pytest.mark.unit
    @pytest.mark.parametrize('failure', [
        subprocess.CalledProcessError(1, 'last'),
        FileNotFoundError('last'),
    ])
    def test_missing_last_command_returns_an_empty_list(self, failure):
        with patch('subprocess.check_output', side_effect=failure):
            assert hs.get_shutdown_events() == []


class TestGetServiceStats:
    @pytest.mark.unit
    def test_indexes_docker_stats_by_container_name(self):
        out = ('{"Name":"capdev","CPUPerc":"3.2%"}\n'
               '{"Name":"captureui","CPUPerc":"0.4%"}\n')
        with patch('subprocess.check_output', return_value=out.encode()):
            stats = hs.get_service_stats()

        assert set(stats) == {'capdev', 'captureui'}
        assert stats['capdev']['CPUPerc'] == '3.2%'

    @pytest.mark.unit
    def test_blank_lines_are_skipped(self):
        out = '\n{"Name":"capdev"}\n\n'
        with patch('subprocess.check_output', return_value=out.encode()):
            assert list(hs.get_service_stats()) == ['capdev']

    @pytest.mark.unit
    def test_a_malformed_line_does_not_lose_the_rest(self):
        out = '{"Name":"capdev"}\nnot json\n{"Name":"vision"}\n'
        with patch('subprocess.check_output', return_value=out.encode()):
            assert set(hs.get_service_stats()) == {'capdev', 'vision'}

    @pytest.mark.unit
    def test_docker_daemon_down_returns_empty(self, capsys):
        err = subprocess.CalledProcessError(1, 'docker stats')
        err.stderr = b'Cannot connect to the Docker daemon at unix:///var/run/docker.sock'
        with patch('subprocess.check_output', side_effect=err):
            assert hs.get_service_stats() == {}
        assert 'Docker daemon is not running' in capsys.readouterr().out

    @pytest.mark.unit
    def test_other_docker_errors_are_reported(self, capsys):
        err = subprocess.CalledProcessError(1, 'docker stats')
        err.stderr = b'permission denied'
        with patch('subprocess.check_output', side_effect=err):
            assert hs.get_service_stats() == {}
        assert 'permission denied' in capsys.readouterr().out

    @pytest.mark.unit
    def test_docker_not_installed_returns_empty(self):
        with patch('subprocess.check_output', side_effect=FileNotFoundError('docker')):
            assert hs.get_service_stats() == {}


class TestGetMetadata:
    @pytest.mark.unit
    def test_extracts_the_teamviewer_id(self):
        out = b'TeamViewer 15.4\n TeamViewer ID: 1 234 567 890\n'
        with patch('subprocess.check_output', return_value=out), \
             patch.object(hs, 'get_software_versions', return_value={}), \
             patch.object(hs.utils_db, 'find', return_value=[]):
            assert hs.get_metadata()['teamviewer_id'] == '1 234 567 890'

    @pytest.mark.unit
    def test_strips_ansi_codes_from_the_id(self):
        out = b'TeamViewer ID: \x1b[0;32m1234567890\x1b[0m\n'
        with patch('subprocess.check_output', return_value=out), \
             patch.object(hs, 'get_software_versions', return_value={}), \
             patch.object(hs.utils_db, 'find', return_value=[]):
            assert hs.get_metadata()['teamviewer_id'] == '1234567890'

    @pytest.mark.unit
    def test_missing_teamviewer_leaves_the_key_out(self):
        with patch('subprocess.check_output', side_effect=FileNotFoundError('teamviewer')), \
             patch.object(hs, 'get_software_versions', return_value={}), \
             patch.object(hs.utils_db, 'find', return_value=[]):
            assert 'teamviewer_id' not in hs.get_metadata()

    @pytest.mark.unit
    def test_software_version_is_always_included(self):
        with patch('subprocess.check_output', side_effect=FileNotFoundError()), \
             patch.object(hs, 'get_software_versions', return_value={'capdev': {}}), \
             patch.object(hs.utils_db, 'find', return_value=[]):
            assert hs.get_metadata()['software_version'] == {'capdev': {}}

    @pytest.mark.unit
    def test_sync_state_is_keyed_by_document_type(self):
        docs = [{'type': 'sync', 'last': 1}, {'type': 'purge_interval', 'hours': 24}]
        with patch('subprocess.check_output', side_effect=FileNotFoundError()), \
             patch.object(hs, 'get_software_versions', return_value={}), \
             patch.object(hs.utils_db, 'find', return_value=docs):
            sync_state = hs.get_metadata()['sync_state']

        assert sync_state == {'sync': {'last': 1}, 'purge_interval': {'hours': 24}}

    @pytest.mark.unit
    def test_unreachable_mongo_omits_sync_state(self):
        with patch('subprocess.check_output', side_effect=FileNotFoundError()), \
             patch.object(hs, 'get_software_versions', return_value={}), \
             patch.object(hs.utils_db, 'find', side_effect=Exception('no mongo')):
            metadata = hs.get_metadata()

        assert 'sync_state' not in metadata
        assert 'software_version' in metadata


DOCKER_PS = 'capdev\tflexiblevision/capdev:1.4.2\ncaptureui\tflexiblevision/captureui:2.0.1\n'


@pytest.fixture
def fvconfig(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'fvconfig.json').write_text(json.dumps({
        'container_check_domain': 'https://functions.test/',
        'latest_stable_ref': 'latest_stable_version',
    }))
    monkeypatch.setenv('HOME', str(home))
    return home


class TestGetSoftwareVersions:
    """running vs latest, as the fleet dashboard reads it."""

    @pytest.mark.unit
    def test_matches_running_containers_to_the_stable_tag(self, fvconfig):
        stable = MagicMock(status_code=200)
        stable.json.return_value = {'x86-backend': '1.5.0', 'x86-frontend': '2.0.1'}

        with patch('subprocess.run', return_value=MagicMock(returncode=0, stdout=DOCKER_PS)), \
             patch('subprocess.check_output', return_value=b'x86_64\n'), \
             patch('requests.post', return_value=stable):
            versions = hs.get_software_versions()

        assert versions == {
            'capdev': {'running': '1.4.2', 'latest': '1.5.0'},
            'captureui': {'running': '2.0.1', 'latest': '2.0.1'},
        }

    @pytest.mark.unit
    def test_arch_is_normalised_before_the_lookup(self, fvconfig):
        stable = MagicMock(status_code=200)
        stable.json.return_value = {'arm-backend': '1.5.0'}

        with patch('subprocess.run', return_value=MagicMock(returncode=0, stdout='capdev\tx:1.4.2\n')), \
             patch('subprocess.check_output', return_value=b'aarch64\n'), \
             patch('requests.post', return_value=stable) as post:
            versions = hs.get_software_versions()

        assert post.call_args[1]['json'] == {'arch': 'arm'}
        assert versions['capdev']['latest'] == '1.5.0'

    @pytest.mark.unit
    def test_unknown_container_reports_its_running_tag_as_latest(self, fvconfig):
        # Nothing in the cloud describes a sidecar, so it must not be shown as
        # out of date forever.
        stable = MagicMock(status_code=200)
        stable.json.return_value = {}

        with patch('subprocess.run', return_value=MagicMock(returncode=0, stdout='mongo\tmongo:4.2\n')), \
             patch('subprocess.check_output', return_value=b'x86_64\n'), \
             patch('requests.post', return_value=stable):
            assert hs.get_software_versions() == {
                'mongo': {'running': '4.2', 'latest': '4.2'}}

    @pytest.mark.unit
    def test_untagged_image_reports_none_running(self, fvconfig):
        stable = MagicMock(status_code=200)
        stable.json.return_value = {}

        with patch('subprocess.run', return_value=MagicMock(returncode=0, stdout='capdev\tcapdev\n')), \
             patch('subprocess.check_output', return_value=b'x86_64\n'), \
             patch('requests.post', return_value=stable):
            assert hs.get_software_versions()['capdev']['running'] is None

    @pytest.mark.unit
    def test_docker_ps_failure_returns_empty(self, fvconfig):
        with patch('subprocess.run', return_value=MagicMock(returncode=1, stdout='')):
            assert hs.get_software_versions() == {}

    @pytest.mark.unit
    def test_docker_missing_returns_empty(self, fvconfig):
        with patch('subprocess.run', side_effect=FileNotFoundError('docker')):
            assert hs.get_software_versions() == {}

    @pytest.mark.unit
    def test_no_running_containers_returns_empty(self, fvconfig):
        with patch('subprocess.run', return_value=MagicMock(returncode=0, stdout='\n')):
            assert hs.get_software_versions() == {}

    @pytest.mark.unit
    def test_malformed_docker_ps_lines_are_skipped(self, fvconfig):
        stable = MagicMock(status_code=200)
        stable.json.return_value = {}

        with patch('subprocess.run',
                   return_value=MagicMock(returncode=0, stdout='no-tab-here\ncapdev\tc:1\n')), \
             patch('subprocess.check_output', return_value=b'x86_64\n'), \
             patch('requests.post', return_value=stable):
            assert list(hs.get_software_versions()) == ['capdev']

    @pytest.mark.unit
    def test_unreachable_cloud_still_reports_running_versions(self, fvconfig):
        # An offline device must keep reporting what it is running rather than
        # returning nothing at all.
        with patch('subprocess.run', return_value=MagicMock(returncode=0, stdout=DOCKER_PS)), \
             patch('subprocess.check_output', return_value=b'x86_64\n'), \
             patch('requests.post', side_effect=ConnectionError('offline')):
            versions = hs.get_software_versions()

        assert versions['capdev'] == {'running': '1.4.2', 'latest': '1.4.2'}

    @pytest.mark.unit
    def test_non_200_from_the_cloud_falls_back_to_running(self, fvconfig):
        with patch('subprocess.run', return_value=MagicMock(returncode=0, stdout=DOCKER_PS)), \
             patch('subprocess.check_output', return_value=b'x86_64\n'), \
             patch('requests.post', return_value=MagicMock(status_code=500)):
            assert hs.get_software_versions()['capdev']['latest'] == '1.4.2'

    @pytest.mark.unit
    def test_missing_fvconfig_falls_back_to_running(self, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path))
        with patch('subprocess.run', return_value=MagicMock(returncode=0, stdout=DOCKER_PS)), \
             patch('subprocess.check_output', return_value=b'x86_64\n'):
            assert hs.get_software_versions()['capdev']['latest'] == '1.4.2'

    @pytest.mark.unit
    def test_every_mapped_container_resolves_to_a_cloud_key(self, fvconfig):
        stdout = ''.join(f'{name}\timg:{i}.0\n'
                         for i, name in enumerate(hs._CLOUD_IMAGE_KEY))
        stable = MagicMock(status_code=200)
        stable.json.return_value = {f'x86-{key}': '9.9.9'
                                    for key in hs._CLOUD_IMAGE_KEY.values()}

        with patch('subprocess.run', return_value=MagicMock(returncode=0, stdout=stdout)), \
             patch('subprocess.check_output', return_value=b'x86_64\n'), \
             patch('requests.post', return_value=stable):
            versions = hs.get_software_versions()

        assert all(v['latest'] == '9.9.9' for v in versions.values())


class TestGetPresets:
    @pytest.mark.unit
    def test_returns_the_preset_documents(self):
        with patch.object(hs.io_presets_db, 'find', return_value=iter([{'name': 'p1'}])):
            assert hs.get_presets() == [{'name': 'p1'}]

    @pytest.mark.unit
    def test_mongo_id_is_projected_away(self):
        with patch.object(hs.io_presets_db, 'find', return_value=iter([])) as find:
            hs.get_presets()
        assert find.call_args[0][1] == {'_id': 0}

    @pytest.mark.unit
    def test_unreachable_mongo_returns_an_empty_list(self, capsys):
        with patch.object(hs.io_presets_db, 'find', side_effect=Exception('no mongo')):
            assert hs.get_presets() == []
        assert 'Could not read presets' in capsys.readouterr().out


class TestSaveMetricsToCsv:
    """CSV rollover for the on-disk metrics history."""

    def _row(self, **overrides):
        row = {k: '' for k in ['cpu', 'memory', 'storage', 'gpu', 'system',
                               'node_name', 'release', 'version', 'machine',
                               'processor']}
        row.update(overrides)
        return row

    @pytest.mark.unit
    def test_writes_a_header_and_one_row_to_a_new_file(self, tmp_path):
        path = str(tmp_path / 'metrics.csv')
        hs.save_metrics_to_csv(self._row(cpu=10), filename=path)

        lines = open(path).read().splitlines()
        assert lines[0].startswith('timestamp,cpu,memory')
        assert len(lines) == 2

    @pytest.mark.unit
    def test_a_timestamp_is_stamped_onto_the_row(self, tmp_path):
        path = str(tmp_path / 'metrics.csv')
        data = self._row(cpu=10)
        hs.save_metrics_to_csv(data, filename=path)

        assert 'timestamp' in data
        assert data['timestamp'] in open(path).read()

    @pytest.mark.unit
    def test_appends_to_an_existing_file(self, tmp_path):
        path = str(tmp_path / 'metrics.csv')
        hs.save_metrics_to_csv(self._row(cpu=1), filename=path)
        hs.save_metrics_to_csv(self._row(cpu=2), filename=path)

        assert len(open(path).read().splitlines()) == 3

    @pytest.mark.unit
    def test_history_is_trimmed_to_the_limit_keeping_the_newest(self, tmp_path):
        path = str(tmp_path / 'metrics.csv')
        for cpu in range(5):
            hs.save_metrics_to_csv(self._row(cpu=cpu), filename=path, limit=3)

        rows = open(path).read().splitlines()[1:]
        assert len(rows) == 3
        assert [r.split(',')[1] for r in rows] == ['2', '3', '4']

    @pytest.mark.unit
    def test_a_file_with_different_columns_is_replaced_not_appended_to(self, tmp_path):
        # Mixing schemas would make every later read fail; the old history is
        # dropped instead.
        path = tmp_path / 'metrics.csv'
        path.write_text('a,b\n1,2\n')
        hs.save_metrics_to_csv(self._row(cpu=1), filename=str(path))

        lines = path.read_text().splitlines()
        assert lines[0].startswith('timestamp,cpu')
        assert len(lines) == 2

    @pytest.mark.unit
    def test_extra_keys_are_rejected(self, tmp_path):
        # _collect_system_metrics builds a dict with 'services', 'uptime' and
        # 'metadata' on it, none of which are columns here. Nothing passes
        # save=True today, so this is latent rather than live - but wiring it
        # up would raise instead of writing.
        path = str(tmp_path / 'metrics.csv')
        with pytest.raises(ValueError):
            hs.save_metrics_to_csv(self._row(cpu=1, services={}), filename=path)


class TestModuleConstants:
    @pytest.mark.unit
    def test_reboot_threshold_is_five_minutes(self):
        assert hs.REBOOT_THRESHOLD_MS == 600000

    @pytest.mark.unit
    def test_cloud_image_key_covers_the_upgradeable_containers(self):
        import version_check
        # Every container the release path can upgrade must be reportable.
        upgradeable = set(version_check.CONTAINERS.values())
        assert upgradeable <= set(hs._CLOUD_IMAGE_KEY)
