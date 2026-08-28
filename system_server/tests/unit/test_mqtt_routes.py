"""VerneMQ bridge management and its health monitor.

The bridge is the device's only link to the cloud broker. Two failure shapes
matter: a health check that reports a dead bridge as healthy (the device goes
quiet and nothing notices), and one that reports a healthy bridge as dead (the
monitor restarts VerneMQ every minute forever). Both are asserted here.
"""
import subprocess
from time import sleep as _sleep

import pytest

_real_sleep = __import__('time').sleep
from unittest.mock import patch, MagicMock, mock_open, call

from routes import mqtt_routes as mq


CONFIG_PATH = '/root/flex-run/setup/mqtt/vernemq-local.conf'

SSL_CONFIG = """\
vmq_bridge.ssl.gke = mqtt.flexiblevision.com:443
vmq_bridge.ssl.gke.client_id = bridge-old
vmq_bridge.ssl.gke.username = device
vmq_bridge.ssl.gke.password = old-token
vmq_bridge.ssl.gke.topic.1 = devices/+/system/update_software in 0
"""

TCP_CONFIG = """\
vmq_bridge.tcp.gke = 10.0.0.5:31883
vmq_bridge.tcp.gke.client_id = backend-local
vmq_bridge.tcp.gke.topic.1 = devices/+/system/update_software in 0
"""


@pytest.fixture(autouse=True)
def reset_monitor_state():
    """The module keeps monitor and metrics state in globals."""
    mq._health_monitor_running = False
    mq._health_monitor_thread = None
    mq._last_publish_count = 0
    mq._last_check_time = 0
    yield
    mq._health_monitor_running = False
    mq._health_monitor_thread = None
    mq._last_publish_count = 0
    mq._last_check_time = 0


def _completed(returncode=0, stdout='', stderr=''):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def client():
    from flask import Flask
    from flask_restx import Api
    app = Flask(__name__)
    app.config['TESTING'] = True
    api = Api(app)
    # register_routes also starts the monitor thread; the endpoints are what
    # is under test here, so the thread start is stubbed out.
    with patch.object(mq, 'start_health_monitor'):
        mq.register_routes(api)
    return app.test_client()


class TestGetAccessToken:
    @pytest.mark.unit
    def test_returns_the_stored_token(self):
        with patch.object(mq.utils_db, 'find_one', return_value={'token': 'abc123'}):
            assert mq.get_access_token() == 'abc123'

    @pytest.mark.unit
    def test_queries_by_document_type(self):
        with patch.object(mq.utils_db, 'find_one', return_value=None) as find:
            mq.get_access_token()
        find.assert_called_once_with({'type': 'access_token'})

    @pytest.mark.unit
    def test_missing_document_returns_none(self):
        with patch.object(mq.utils_db, 'find_one', return_value=None):
            assert mq.get_access_token() is None

    @pytest.mark.unit
    def test_document_without_a_token_field_returns_none(self):
        with patch.object(mq.utils_db, 'find_one', return_value={'type': 'access_token'}):
            assert mq.get_access_token() is None

    @pytest.mark.unit
    def test_unreachable_mongo_returns_none(self):
        with patch.object(mq.utils_db, 'find_one', side_effect=Exception('no mongo')):
            assert mq.get_access_token() is None


class TestGetDeviceId:
    @pytest.mark.unit
    def test_returns_the_stored_id(self):
        with patch.object(mq.utils_db, 'find_one', return_value={'id': 'dev-42'}):
            assert mq.get_device_id() == 'dev-42'

    @pytest.mark.unit
    def test_queries_by_document_type(self):
        with patch.object(mq.utils_db, 'find_one', return_value=None) as find:
            mq.get_device_id()
        find.assert_called_once_with({'type': 'device_id'})

    @pytest.mark.unit
    def test_missing_document_returns_none(self):
        with patch.object(mq.utils_db, 'find_one', return_value=None):
            assert mq.get_device_id() is None

    @pytest.mark.unit
    def test_unreachable_mongo_returns_none(self):
        with patch.object(mq.utils_db, 'find_one', side_effect=Exception('no mongo')):
            assert mq.get_device_id() is None


def _written(opener):
    """Concatenate everything written through a mock_open handle."""
    return ''.join(c.args[0] for c in opener().write.call_args_list)


class TestUpdateBridgeConfig:
    @pytest.mark.unit
    def test_replaces_the_password_with_the_new_token(self):
        opener = mock_open(read_data=SSL_CONFIG)
        with patch('os.path.exists', return_value=True), \
             patch('builtins.open', opener):
            result = mq.update_bridge_config('new-token')

        assert result == {'success': True, 'config_updated': True}
        assert 'vmq_bridge.ssl.gke.password = new-token' in _written(opener)
        assert 'old-token' not in _written(opener)

    @pytest.mark.unit
    def test_sets_the_client_id_from_the_device_id(self):
        opener = mock_open(read_data=SSL_CONFIG)
        with patch('os.path.exists', return_value=True), \
             patch('builtins.open', opener):
            mq.update_bridge_config('new-token', 'dev-42')

        assert 'vmq_bridge.ssl.gke.client_id = bridge-dev-42' in _written(opener)

    @pytest.mark.unit
    def test_client_id_is_left_alone_without_a_device_id(self):
        opener = mock_open(read_data=SSL_CONFIG)
        with patch('os.path.exists', return_value=True), \
             patch('builtins.open', opener):
            mq.update_bridge_config('new-token')

        assert 'vmq_bridge.ssl.gke.client_id = bridge-old' in _written(opener)

    @pytest.mark.unit
    def test_other_settings_are_preserved(self):
        opener = mock_open(read_data=SSL_CONFIG)
        with patch('os.path.exists', return_value=True), \
             patch('builtins.open', opener):
            mq.update_bridge_config('new-token', 'dev-42')

        written = _written(opener)
        assert 'vmq_bridge.ssl.gke = mqtt.flexiblevision.com:443' in written
        assert 'vmq_bridge.ssl.gke.username = device' in written
        assert 'devices/+/system/update_software in 0' in written

    @pytest.mark.unit
    def test_local_tcp_bridge_is_left_untouched(self):
        # The tcp bridge authenticates by client_id with no token. Rewriting it
        # would break a working local-cloud install.
        opener = mock_open(read_data=TCP_CONFIG)
        with patch('os.path.exists', return_value=True), \
             patch('builtins.open', opener):
            result = mq.update_bridge_config('new-token', 'dev-42')

        assert result == {'success': True,
                          'skipped': 'local-cloud tcp bridge (no token needed)'}
        assert opener().write.call_count == 0

    @pytest.mark.unit
    def test_missing_config_file_is_an_error(self):
        with patch('os.path.exists', return_value=False):
            result = mq.update_bridge_config('new-token')

        assert result['success'] is False
        assert CONFIG_PATH in result['error']

    @pytest.mark.unit
    def test_unreadable_config_is_reported_not_raised(self):
        with patch('os.path.exists', return_value=True), \
             patch('builtins.open', side_effect=PermissionError('denied')):
            result = mq.update_bridge_config('new-token')

        assert result['success'] is False
        assert 'denied' in result['error']

    @pytest.mark.unit
    def test_a_config_with_no_password_line_still_writes(self):
        opener = mock_open(read_data='vmq_bridge.ssl.gke = host:443\n')
        with patch('os.path.exists', return_value=True), \
             patch('builtins.open', opener):
            assert mq.update_bridge_config('new-token')['success'] is True


class TestRestartVernemq:
    @pytest.mark.unit
    def test_success(self):
        with patch('subprocess.run', return_value=_completed(0)) as run:
            assert mq.restart_vernemq() == {'success': True, 'message': 'VerneMQ restarted'}
        assert run.call_args[0][0] == ['docker', 'restart', mq.VERNEMQ_CONTAINER]

    @pytest.mark.unit
    def test_failure_returns_stderr(self):
        with patch('subprocess.run', return_value=_completed(1, '', 'no such container')):
            assert mq.restart_vernemq() == {'success': False, 'error': 'no such container'}

    @pytest.mark.unit
    def test_timeout_is_reported(self):
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('docker', 60)):
            assert mq.restart_vernemq() == {'success': False, 'error': 'Restart timed out'}

    @pytest.mark.unit
    def test_docker_missing_is_reported(self):
        with patch('subprocess.run', side_effect=FileNotFoundError('docker')):
            assert mq.restart_vernemq()['success'] is False

    @pytest.mark.unit
    def test_restart_is_bounded_by_a_timeout(self):
        # An unbounded docker restart would wedge the health monitor thread.
        with patch('subprocess.run', return_value=_completed(0)) as run:
            mq.restart_vernemq()
        assert run.call_args[1]['timeout'] == 60


class TestGetBridgeStatus:
    @pytest.mark.unit
    def test_returns_vmq_admin_output(self):
        with patch('subprocess.run', return_value=_completed(0, 'gke | true |')) as run:
            assert mq.get_bridge_status() == {'success': True, 'status': 'gke | true |'}
        assert run.call_args[0][0] == [
            'docker', 'exec', mq.VERNEMQ_CONTAINER,
            '/vernemq/bin/vmq-admin', 'bridge', 'show']

    @pytest.mark.unit
    def test_failure_returns_stderr(self):
        with patch('subprocess.run', return_value=_completed(1, '', 'not running')):
            assert mq.get_bridge_status() == {'success': False, 'error': 'not running'}

    @pytest.mark.unit
    def test_exception_is_reported(self):
        with patch('subprocess.run', side_effect=OSError('docker gone')):
            assert mq.get_bridge_status()['success'] is False


class TestGetBridgeMetrics:
    @pytest.mark.unit
    def test_parses_bridge_counters_into_ints(self):
        out = ('counter.gke_vmq_bridge_publish_in_0 = 42\n'
               'counter.gke_vmq_bridge_publish_out_0 = 17\n'
               'counter.unrelated_metric = 9\n')
        with patch('subprocess.run', return_value=_completed(0, out)):
            metrics = mq.get_bridge_metrics()['metrics']

        assert metrics == {'counter.gke_vmq_bridge_publish_in_0': 42,
                           'counter.gke_vmq_bridge_publish_out_0': 17}

    @pytest.mark.unit
    def test_non_numeric_values_are_kept_as_strings(self):
        with patch('subprocess.run', return_value=_completed(0, 'gauge.bridge_state = up\n')):
            assert mq.get_bridge_metrics()['metrics'] == {'gauge.bridge_state': 'up'}

    @pytest.mark.unit
    def test_lines_without_a_single_equals_are_skipped(self):
        out = 'bridge no equals here\nbridge a = b = c\n'
        with patch('subprocess.run', return_value=_completed(0, out)):
            assert mq.get_bridge_metrics()['metrics'] == {}

    @pytest.mark.unit
    def test_failure_returns_stderr(self):
        with patch('subprocess.run', return_value=_completed(1, '', 'exec failed')):
            assert mq.get_bridge_metrics() == {'success': False, 'error': 'exec failed'}

    @pytest.mark.unit
    def test_exception_is_reported(self):
        with patch('subprocess.run', side_effect=OSError('boom')):
            assert mq.get_bridge_metrics()['success'] is False


class TestGetBridgePort:
    @pytest.mark.unit
    def test_reads_the_ssl_endpoint_port(self):
        with patch('builtins.open', mock_open(read_data=SSL_CONFIG)):
            assert mq.get_bridge_port() == '443'

    @pytest.mark.unit
    def test_reads_the_tcp_endpoint_port(self):
        with patch('builtins.open', mock_open(read_data=TCP_CONFIG)):
            assert mq.get_bridge_port() == '31883'

    @pytest.mark.unit
    def test_unreadable_config_falls_back_to_443(self):
        with patch('builtins.open', side_effect=IOError('no config')):
            assert mq.get_bridge_port() == '443'

    @pytest.mark.unit
    def test_config_without_an_endpoint_falls_back_to_443(self):
        with patch('builtins.open', mock_open(read_data='# nothing here\n')):
            assert mq.get_bridge_port() == '443'


class TestIsLocalBridge:
    @pytest.mark.unit
    def test_tcp_config_is_local(self):
        with patch('builtins.open', mock_open(read_data=TCP_CONFIG)):
            assert mq._is_local_bridge() is True

    @pytest.mark.unit
    def test_ssl_config_is_not_local(self):
        with patch('builtins.open', mock_open(read_data=SSL_CONFIG)):
            assert mq._is_local_bridge() is False

    @pytest.mark.unit
    def test_unreadable_config_is_not_local(self):
        with patch('builtins.open', side_effect=IOError('no config')):
            assert mq._is_local_bridge() is False


class TestCheckTcpConnectionToCloud:
    @pytest.mark.unit
    def test_established_beam_connection_on_the_bridge_port(self):
        netstat = ('Proto Recv-Q Send-Q Local Address Foreign Address State PID\n'
                   'tcp 0 0 10.0.0.2:44112 34.1.2.3:443 ESTABLISHED 1/beam.smp\n')
        with patch.object(mq, 'get_bridge_port', return_value='443'), \
             patch('subprocess.run', return_value=_completed(0, netstat)):
            assert mq.check_tcp_connection_to_cloud() is True

    @pytest.mark.unit
    def test_connection_on_a_different_port_does_not_count(self):
        netstat = 'tcp 0 0 10.0.0.2:44112 34.1.2.3:1883 ESTABLISHED 1/beam.smp\n'
        with patch.object(mq, 'get_bridge_port', return_value='443'), \
             patch('subprocess.run', return_value=_completed(0, netstat)):
            assert mq.check_tcp_connection_to_cloud() is False

    @pytest.mark.unit
    def test_a_connection_still_being_established_does_not_count(self):
        netstat = 'tcp 0 0 10.0.0.2:44112 34.1.2.3:443 SYN_SENT 1/beam.smp\n'
        with patch.object(mq, 'get_bridge_port', return_value='443'), \
             patch('subprocess.run', return_value=_completed(0, netstat)):
            assert mq.check_tcp_connection_to_cloud() is False

    @pytest.mark.unit
    def test_another_process_holding_the_port_does_not_count(self):
        netstat = 'tcp 0 0 10.0.0.2:44112 34.1.2.3:443 ESTABLISHED 9/curl\n'
        with patch.object(mq, 'get_bridge_port', return_value='443'), \
             patch('subprocess.run', return_value=_completed(0, netstat)):
            assert mq.check_tcp_connection_to_cloud() is False

    @pytest.mark.unit
    def test_netstat_failure_is_not_a_connection(self):
        with patch.object(mq, 'get_bridge_port', return_value='443'), \
             patch('subprocess.run', return_value=_completed(1, '', 'no netstat')):
            assert mq.check_tcp_connection_to_cloud() is False

    @pytest.mark.unit
    def test_exception_is_not_a_connection(self):
        with patch.object(mq, 'get_bridge_port', return_value='443'), \
             patch('subprocess.run', side_effect=OSError('boom')):
            assert mq.check_tcp_connection_to_cloud() is False


class TestIsBridgeHealthy:
    @pytest.mark.unit
    def test_connected_cloud_bridge_is_healthy(self):
        with patch.object(mq, 'get_bridge_status', return_value={'success': True, 'status': 'gke'}), \
             patch.object(mq, '_is_local_bridge', return_value=False), \
             patch.object(mq, 'check_tcp_connection_to_cloud', return_value=True), \
             patch.object(mq, 'get_bridge_metrics', return_value={'success': False}):
            healthy, reason = mq.is_bridge_healthy()

        assert healthy is True
        assert reason == 'Bridge connected (metrics unavailable)'

    @pytest.mark.unit
    def test_unreadable_status_is_unhealthy(self):
        with patch.object(mq, 'get_bridge_status',
                          return_value={'success': False, 'error': 'container down'}):
            healthy, reason = mq.is_bridge_healthy()

        assert healthy is False
        assert 'container down' in reason

    @pytest.mark.unit
    def test_unconfigured_bridge_is_unhealthy(self):
        with patch.object(mq, 'get_bridge_status',
                          return_value={'success': True, 'status': 'no bridges'}):
            healthy, reason = mq.is_bridge_healthy()

        assert healthy is False
        assert reason == "Bridge 'gke' not configured"

    @pytest.mark.unit
    def test_configured_local_bridge_skips_the_netstat_probe(self):
        # The local-cloud image has no netstat, so probing it would report a
        # working bridge as dead and restart VerneMQ every minute.
        with patch.object(mq, 'get_bridge_status', return_value={'success': True, 'status': 'gke'}), \
             patch.object(mq, '_is_local_bridge', return_value=True), \
             patch.object(mq, 'check_tcp_connection_to_cloud') as probe:
            healthy, reason = mq.is_bridge_healthy()

        assert healthy is True
        assert reason == 'Bridge configured (local-cloud tcp)'
        probe.assert_not_called()

    @pytest.mark.unit
    def test_no_tcp_connection_is_unhealthy(self):
        with patch.object(mq, 'get_bridge_status', return_value={'success': True, 'status': 'gke'}), \
             patch.object(mq, '_is_local_bridge', return_value=False), \
             patch.object(mq, 'check_tcp_connection_to_cloud', return_value=False), \
             patch.object(mq, 'get_bridge_port', return_value='443'):
            healthy, reason = mq.is_bridge_healthy()

        assert healthy is False
        assert 'No TCP connection' in reason and '443' in reason

    @pytest.mark.unit
    def test_first_metrics_check_records_a_baseline(self):
        metrics = {'success': True, 'metrics': {
            'counter.gke_vmq_bridge_publish_in_0': 10,
            'counter.gke_vmq_bridge_publish_out_0': 5}}

        with patch.object(mq, 'get_bridge_status', return_value={'success': True, 'status': 'gke'}), \
             patch.object(mq, '_is_local_bridge', return_value=False), \
             patch.object(mq, 'check_tcp_connection_to_cloud', return_value=True), \
             patch.object(mq, 'get_bridge_metrics', return_value=metrics):
            healthy, reason = mq.is_bridge_healthy()

        assert healthy is True
        assert reason == 'Bridge connected (initial check)'
        assert mq._last_publish_count == 15
        assert mq._last_check_time != 0

    @pytest.mark.unit
    def test_subsequent_check_reports_the_message_delta(self):
        mq._last_publish_count = 15
        mq._last_check_time = 1000.0
        metrics = {'success': True, 'metrics': {
            'counter.gke_vmq_bridge_publish_in_0': 20,
            'counter.gke_vmq_bridge_publish_out_0': 12}}

        with patch.object(mq, 'get_bridge_status', return_value={'success': True, 'status': 'gke'}), \
             patch.object(mq, '_is_local_bridge', return_value=False), \
             patch.object(mq, 'check_tcp_connection_to_cloud', return_value=True), \
             patch.object(mq, 'get_bridge_metrics', return_value=metrics), \
             patch('routes.mqtt_routes.time.time', return_value=1030.0):
            healthy, reason = mq.is_bridge_healthy()

        assert healthy is True
        assert reason == 'Bridge connected: 17 msgs in 30s'
        assert mq._last_publish_count == 32

    @pytest.mark.unit
    def test_a_silent_but_connected_bridge_is_still_healthy(self):
        # Zero messages is normal on an idle line and must not trigger a
        # restart loop.
        mq._last_publish_count = 15
        mq._last_check_time = 1000.0
        metrics = {'success': True, 'metrics': {
            'counter.gke_vmq_bridge_publish_in_0': 15,
            'counter.gke_vmq_bridge_publish_out_0': 0}}

        with patch.object(mq, 'get_bridge_status', return_value={'success': True, 'status': 'gke'}), \
             patch.object(mq, '_is_local_bridge', return_value=False), \
             patch.object(mq, 'check_tcp_connection_to_cloud', return_value=True), \
             patch.object(mq, 'get_bridge_metrics', return_value=metrics), \
             patch('routes.mqtt_routes.time.time', return_value=1030.0):
            healthy, reason = mq.is_bridge_healthy()

        assert healthy is True
        assert '0 msgs' in reason

    @pytest.mark.unit
    def test_bridge_name_match_is_case_insensitive(self):
        with patch.object(mq, 'get_bridge_status', return_value={'success': True, 'status': 'GKE bridge'}), \
             patch.object(mq, '_is_local_bridge', return_value=True):
            assert mq.is_bridge_healthy()[0] is True

    @pytest.mark.unit
    def test_unexpected_exception_is_unhealthy_not_a_crash(self):
        # This runs on the monitor thread; an escaping exception would kill it
        # and leave the bridge unmonitored for the life of the process.
        with patch.object(mq, 'get_bridge_status', side_effect=RuntimeError('boom')):
            healthy, reason = mq.is_bridge_healthy()

        assert healthy is False
        assert 'Health check error: boom' in reason


class TestDoBridgeRefresh:
    @pytest.mark.unit
    def test_updates_config_then_restarts(self):
        with patch.object(mq, 'get_access_token', return_value='tok'), \
             patch.object(mq, 'get_device_id', return_value='dev-1'), \
             patch.object(mq, 'update_bridge_config', return_value={'success': True}) as update, \
             patch.object(mq, 'restart_vernemq', return_value={'success': True}) as restart:
            assert mq._do_bridge_refresh() is True

        update.assert_called_once_with('tok', 'dev-1')
        restart.assert_called_once()

    @pytest.mark.unit
    def test_without_a_token_nothing_is_touched(self):
        with patch.object(mq, 'get_access_token', return_value=None), \
             patch.object(mq, 'update_bridge_config') as update, \
             patch.object(mq, 'restart_vernemq') as restart:
            assert mq._do_bridge_refresh() is False

        update.assert_not_called()
        restart.assert_not_called()

    @pytest.mark.unit
    def test_a_failed_config_write_does_not_restart_vernemq(self):
        # Restarting onto an unchanged config would drop the bridge for no gain.
        with patch.object(mq, 'get_access_token', return_value='tok'), \
             patch.object(mq, 'get_device_id', return_value='dev-1'), \
             patch.object(mq, 'update_bridge_config',
                          return_value={'success': False, 'error': 'read only'}), \
             patch.object(mq, 'restart_vernemq') as restart:
            assert mq._do_bridge_refresh() is False

        restart.assert_not_called()

    @pytest.mark.unit
    def test_a_failed_restart_is_reported(self):
        with patch.object(mq, 'get_access_token', return_value='tok'), \
             patch.object(mq, 'get_device_id', return_value='dev-1'), \
             patch.object(mq, 'update_bridge_config', return_value={'success': True}), \
             patch.object(mq, 'restart_vernemq', return_value={'success': False, 'error': 'x'}):
            assert mq._do_bridge_refresh() is False


class TestHealthMonitorLoop:
    """The loop is driven synchronously; sleep is the tick."""

    def _run_loop(self, health, checks=3, refresh=True):
        """Run the loop for exactly `checks` health checks, then let it exit.

        The loop sleeps before each check, so stopping on the Nth sleep would
        cut the Nth check. The stop flag is set from the check itself instead.
        """
        seen = []
        performed = []

        def sleep(seconds):
            # Recorded, never raising: the loop is stopped from the health
            # check below instead. Background driver threads are excluded from
            # the record, and must still sleep for real - returning instantly
            # turns their polling loops into a busy-spin.  real-sleep-ok
            import threading
            import time as _t
            if threading.current_thread() is not threading.main_thread():
                return _real_sleep(seconds)
            seen.append(seconds)
            assert len(seen) < 50, 'monitor loop did not terminate'

        def checked():
            performed.append(1)
            if len(performed) >= checks:
                mq._health_monitor_running = False
            return health()

        mq._health_monitor_running = True
        with patch('routes.mqtt_routes.time.sleep',  # thread-aware-sleep
                   side_effect=sleep), \
             patch.object(mq, 'is_bridge_healthy', side_effect=checked), \
             patch.object(mq, '_do_bridge_refresh', return_value=refresh) as do_refresh:
            mq._health_monitor_loop()
        return seen, do_refresh

    @pytest.mark.unit
    def test_a_healthy_bridge_is_never_refreshed(self):
        _, refresh = self._run_loop(lambda: (True, 'ok'))
        refresh.assert_not_called()

    @pytest.mark.unit
    def test_ticks_on_the_configured_interval(self):
        seen, _ = self._run_loop(lambda: (True, 'ok'))
        assert seen == [mq.HEALTH_CHECK_INTERVAL] * 3

    @pytest.mark.unit
    def test_a_single_failure_does_not_trigger_a_refresh(self):
        # One missed check is a blip; restarting on it would churn the bridge.
        results = iter([(False, 'down'), (True, 'ok'), (True, 'ok')])
        _, refresh = self._run_loop(lambda: next(results))
        refresh.assert_not_called()

    @pytest.mark.unit
    def test_two_consecutive_failures_trigger_a_refresh(self):
        _, refresh = self._run_loop(lambda: (False, 'down'), checks=2)
        refresh.assert_called_once()

    @pytest.mark.unit
    def test_the_failure_counter_resets_after_recovery(self):
        results = iter([(False, 'down'), (True, 'ok'), (False, 'down'), (True, 'ok')])
        _, refresh = self._run_loop(lambda: next(results), checks=4)
        refresh.assert_not_called()

    @pytest.mark.unit
    def test_a_successful_refresh_waits_for_vernemq_to_come_back(self):
        seen, _ = self._run_loop(lambda: (False, 'down'), checks=2, refresh=True)
        # The 15s settle after a restart, on top of the interval ticks.
        assert 15 in seen

    @pytest.mark.unit
    def test_a_failed_refresh_is_retried_on_the_next_tick(self):
        _, refresh = self._run_loop(lambda: (False, 'down'), checks=3, refresh=False)
        assert refresh.call_count >= 2

    @pytest.mark.unit
    def test_stopping_between_the_sleep_and_the_check_exits_cleanly(self):
        def sleep(seconds):
            import threading
            if threading.current_thread() is not threading.main_thread():
                return _real_sleep(seconds)
            mq._health_monitor_running = False

        mq._health_monitor_running = True
        with patch('routes.mqtt_routes.time.sleep',  # thread-aware-sleep
                   side_effect=sleep), \
             patch.object(mq, 'is_bridge_healthy') as check:
            mq._health_monitor_loop()

        check.assert_not_called()

    @pytest.mark.unit
    def test_an_exception_in_a_check_does_not_kill_the_loop(self):
        results = iter([RuntimeError('boom'), (True, 'ok'), (True, 'ok')])

        def health():
            r = next(results)
            if isinstance(r, Exception):
                raise r
            return r

        seen, _ = self._run_loop(health, checks=3)
        assert len(seen) == 3

    @pytest.mark.unit
    def test_metrics_tracking_is_reset_on_entry(self):
        mq._last_check_time = 999.0
        mq._last_publish_count = 999

        def sleep(seconds):
            import threading
            if threading.current_thread() is not threading.main_thread():
                return _real_sleep(seconds)
            mq._health_monitor_running = False

        mq._health_monitor_running = True
        with patch('routes.mqtt_routes.time.sleep',  # thread-aware-sleep
                   side_effect=sleep):
            mq._health_monitor_loop()

        assert mq._last_check_time == 0
        assert mq._last_publish_count == 0


class TestStartStopHealthMonitor:
    @pytest.mark.unit
    def test_start_launches_a_daemon_thread(self):
        with patch('routes.mqtt_routes.threading.Thread') as thread:
            mq.start_health_monitor()

        assert mq._health_monitor_running is True
        assert thread.call_args[1]['daemon'] is True
        assert thread.call_args[1]['target'] is mq._health_monitor_loop
        thread.return_value.start.assert_called_once()

    @pytest.mark.unit
    def test_start_is_idempotent(self):
        # register_routes runs on every import path; a second monitor thread
        # would double every restart decision.
        mq._health_monitor_running = True
        with patch('routes.mqtt_routes.threading.Thread') as thread:
            mq.start_health_monitor()
        thread.assert_not_called()

    @pytest.mark.unit
    def test_stop_clears_the_running_flag(self):
        mq._health_monitor_running = True
        mq.stop_health_monitor()
        assert mq._health_monitor_running is False

    @pytest.mark.unit
    @pytest.mark.timeout(15)
    def test_a_real_thread_starts_and_stops(self):
        # A short real sleep rather than an instant return: the loop has no
        # other yield point, and a zero-cost sleep turns this into a spin that
        # starves the main thread.
        with patch.object(mq, 'is_bridge_healthy', return_value=(True, 'ok')), \
             patch('routes.mqtt_routes.time.sleep',
                   lambda seconds: _sleep(0.01)):  # thread-aware-sleep
            mq.start_health_monitor()
            thread = mq._health_monitor_thread
            assert thread.is_alive()
            mq.stop_health_monitor()
            thread.join(5)

        assert not thread.is_alive()


class TestBridgeStatusEndpoint:
    @pytest.mark.integration
    def test_reports_bridge_token_presence_and_device(self, client):
        with patch.object(mq, 'get_bridge_status', return_value={'success': True, 'status': 'gke'}), \
             patch.object(mq, 'get_access_token', return_value='tok'), \
             patch.object(mq, 'get_device_id', return_value='dev-1'):
            body = client.get('/mqtt/bridge/status').get_json()

        assert body == {'bridge': {'success': True, 'status': 'gke'},
                        'has_token': True, 'device_id': 'dev-1'}

    @pytest.mark.integration
    def test_the_token_itself_is_never_returned(self, client):
        with patch.object(mq, 'get_bridge_status', return_value={'success': True, 'status': ''}), \
             patch.object(mq, 'get_access_token', return_value='super-secret'), \
             patch.object(mq, 'get_device_id', return_value=None):
            body = client.get('/mqtt/bridge/status').get_json()

        assert 'super-secret' not in str(body)
        assert body['has_token'] is True

    @pytest.mark.integration
    def test_missing_token_is_reported_as_false(self, client):
        with patch.object(mq, 'get_bridge_status', return_value={'success': False, 'error': 'x'}), \
             patch.object(mq, 'get_access_token', return_value=None), \
             patch.object(mq, 'get_device_id', return_value=None):
            assert client.get('/mqtt/bridge/status').get_json()['has_token'] is False


class TestBridgeRefreshEndpoint:
    @pytest.mark.integration
    def test_updates_and_restarts(self, client):
        with patch.object(mq, 'get_access_token', return_value='tok'), \
             patch.object(mq, 'get_device_id', return_value='dev-1'), \
             patch.object(mq, 'update_bridge_config', return_value={'success': True}), \
             patch.object(mq, 'restart_vernemq', return_value={'success': True}):
            response = client.post('/mqtt/bridge/refresh')

        assert response.status_code == 200
        assert response.get_json()['success'] is True
        assert response.get_json()['device_id'] == 'dev-1'

    @pytest.mark.integration
    def test_no_token_is_a_404(self, client):
        with patch.object(mq, 'get_access_token', return_value=None):
            response = client.post('/mqtt/bridge/refresh')

        assert response.status_code == 404
        assert response.get_json() == {'success': False, 'error': 'No access token found'}

    @pytest.mark.integration
    def test_config_failure_is_a_500_and_skips_the_restart(self, client):
        with patch.object(mq, 'get_access_token', return_value='tok'), \
             patch.object(mq, 'get_device_id', return_value='dev-1'), \
             patch.object(mq, 'update_bridge_config',
                          return_value={'success': False, 'error': 'read only'}), \
             patch.object(mq, 'restart_vernemq') as restart:
            response = client.post('/mqtt/bridge/refresh')

        assert response.status_code == 500
        restart.assert_not_called()

    @pytest.mark.integration
    def test_restart_failure_is_a_500(self, client):
        with patch.object(mq, 'get_access_token', return_value='tok'), \
             patch.object(mq, 'get_device_id', return_value='dev-1'), \
             patch.object(mq, 'update_bridge_config', return_value={'success': True}), \
             patch.object(mq, 'restart_vernemq', return_value={'success': False, 'error': 'x'}):
            assert client.post('/mqtt/bridge/refresh').status_code == 500


class TestBridgeTokenEndpoint:
    @pytest.mark.integration
    def test_sets_the_supplied_token_and_restarts_by_default(self, client):
        with patch.object(mq, 'get_device_id', return_value='dev-1'), \
             patch.object(mq, 'update_bridge_config',
                          return_value={'success': True, 'config_updated': True}) as update, \
             patch.object(mq, 'restart_vernemq', return_value={'success': True}) as restart:
            response = client.post('/mqtt/bridge/token', json={'token': 'manual'})

        assert response.status_code == 200
        update.assert_called_once_with('manual', 'dev-1')
        restart.assert_called_once()

    @pytest.mark.integration
    def test_restart_can_be_suppressed(self, client):
        with patch.object(mq, 'get_device_id', return_value='dev-1'), \
             patch.object(mq, 'update_bridge_config', return_value={'success': True}), \
             patch.object(mq, 'restart_vernemq') as restart:
            response = client.post('/mqtt/bridge/token',
                                   json={'token': 'manual', 'restart': False})

        assert response.status_code == 200
        restart.assert_not_called()

    @pytest.mark.integration
    def test_missing_token_is_a_400(self, client):
        with patch.object(mq, 'update_bridge_config') as update:
            response = client.post('/mqtt/bridge/token', json={})

        assert response.status_code == 400
        update.assert_not_called()

    @pytest.mark.integration
    def test_empty_body_is_a_400(self, client):
        response = client.post('/mqtt/bridge/token',
                               data='', content_type='application/json')
        assert response.status_code == 400

    @pytest.mark.integration
    def test_config_failure_is_a_500(self, client):
        with patch.object(mq, 'get_device_id', return_value='dev-1'), \
             patch.object(mq, 'update_bridge_config',
                          return_value={'success': False, 'error': 'nope'}):
            assert client.post('/mqtt/bridge/token',
                               json={'token': 'manual'}).status_code == 500


class TestBridgeHealthEndpoint:
    @pytest.mark.integration
    def test_reports_health_metrics_and_monitor_state(self, client):
        with patch.object(mq, 'is_bridge_healthy', return_value=(True, 'connected')), \
             patch.object(mq, 'get_bridge_metrics',
                          return_value={'success': True, 'metrics': {'a': 1}}):
            body = client.get('/mqtt/bridge/health').get_json()

        assert body == {'healthy': True, 'reason': 'connected', 'metrics': {'a': 1},
                        'monitor_running': False,
                        'check_interval': mq.HEALTH_CHECK_INTERVAL}

    @pytest.mark.integration
    def test_unavailable_metrics_are_reported_as_empty(self, client):
        with patch.object(mq, 'is_bridge_healthy', return_value=(False, 'down')), \
             patch.object(mq, 'get_bridge_metrics', return_value={'success': False}):
            body = client.get('/mqtt/bridge/health').get_json()

        assert body['healthy'] is False
        assert body['metrics'] == {}


class TestBridgeMonitorEndpoint:
    @pytest.mark.integration
    def test_start_action(self, client):
        with patch.object(mq, 'start_health_monitor') as start:
            response = client.post('/mqtt/bridge/monitor', json={'action': 'start'})

        assert response.status_code == 200
        start.assert_called_once()

    @pytest.mark.integration
    def test_start_is_the_default_action(self, client):
        with patch.object(mq, 'start_health_monitor') as start:
            client.post('/mqtt/bridge/monitor', json={})
        start.assert_called_once()

    @pytest.mark.integration
    def test_stop_action(self, client):
        with patch.object(mq, 'stop_health_monitor') as stop:
            response = client.post('/mqtt/bridge/monitor', json={'action': 'stop'})

        assert response.status_code == 200
        stop.assert_called_once()

    @pytest.mark.integration
    def test_unknown_action_is_a_400(self, client):
        with patch.object(mq, 'start_health_monitor') as start, \
             patch.object(mq, 'stop_health_monitor') as stop:
            response = client.post('/mqtt/bridge/monitor', json={'action': 'explode'})

        assert response.status_code == 400
        start.assert_not_called()
        stop.assert_not_called()


class TestRegisterRoutes:
    @pytest.mark.integration
    def test_registers_every_bridge_path(self, client):
        rules = {r.rule for r in client.application.url_map.iter_rules()}
        assert {'/mqtt/bridge/status', '/mqtt/bridge/refresh', '/mqtt/bridge/token',
                '/mqtt/bridge/health', '/mqtt/bridge/monitor'} <= rules

    @pytest.mark.integration
    def test_registration_starts_the_health_monitor(self):
        from flask import Flask
        from flask_restx import Api
        api = Api(Flask(__name__))

        with patch.object(mq, 'start_health_monitor') as start:
            mq.register_routes(api)

        start.assert_called_once()
