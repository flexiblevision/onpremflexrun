"""
MQTT Bridge Management Routes

Manages the VerneMQ bridge connection to cloud MQTT broker.
Updates bridge credentials when access token refreshes.
Includes health monitoring to auto-reconnect if bridge disconnects.
"""

import os
import re
import socket
import struct
import subprocess
import logging
import threading
import time
from flask import request
from flask_restx import Resource
from pymongo import MongoClient
import auth

log = logging.getLogger(__name__)

# Health monitor settings
HEALTH_CHECK_INTERVAL = 30  # Check every 30 seconds
METRICS_STALE_THRESHOLD = 60  # Consider bridge stale if no new messages in 1 minute
_health_monitor_thread = None
_health_monitor_running = False
_last_publish_count = 0
_last_check_time = 0
LOCAL_BRIDGE_PORT = 31883
LOCAL_REFRESH_BACKOFF_MAX = 600
_local_refresh_backoff = 0
_local_refresh_not_before = 0.0

# MongoDB connection for reading tokens
client = MongoClient("172.17.0.1")
utils_db = client["fvonprem"]["utils"]

# VerneMQ container name
VERNEMQ_CONTAINER = "vernemq"

# Written by build.sh --allow-placeholder / shown in the README.
PLACEHOLDER_PASSWORDS = {"your-bridge-secret", "your-bridge-password"}


def get_access_token():
    """Get the current access token from MongoDB"""
    try:
        token_doc = utils_db.find_one({'type': 'access_token'})
        if token_doc and 'token' in token_doc:
            return token_doc['token']
    except Exception as e:
        log.error(f"Failed to get access_token: {e}")
    return None


def get_device_id():
    """Get the device ID from MongoDB"""
    try:
        device_doc = utils_db.find_one({'type': 'device_id'})
        if device_doc and 'id' in device_doc:
            return device_doc['id']
    except Exception as e:
        log.error(f"Failed to get device_id: {e}")
    return None


def _host_of(address):
    if not address:
        return None
    host = re.sub(r'^[a-zA-Z]+://', '', str(address)).split('/')[0]
    return (host.rsplit(':', 1)[0] if ':' in host else host) or None


def expected_bridge_address():
    """The broker of the cloud this device now talks to, or None if unknown."""
    try:
        import cloud_env
        if _is_local_bridge():
            host = _host_of(cloud_env.get_cloud_domain())
            return f'{host}:{LOCAL_BRIDGE_PORT}' if host else None
        return cloud_env.get_bridge_address()
    except Exception as e:
        log.warning(f"Failed to resolve bridge address: {e}")
        return None


def _read_bridge_setting(suffix):
    try:
        with open("/root/flex-run/setup/mqtt/vernemq-local.conf") as f:
            for line in f:
                m = re.match(r'\s*vmq_bridge\.(?:ssl|tcp)\.gke' + re.escape(suffix) + r'\s*=\s*(\S+)', line)
                if m:
                    return m.group(1)
    except Exception as e:
        log.warning(f"Failed to read bridge setting gke{suffix}: {e}")
    return None


def get_configured_bridge_address():
    """The broker the cloud bridge config points at, or None."""
    return _read_bridge_setting('')


def get_configured_client_id():
    return _read_bridge_setting('.client_id')


def _this_host_addresses():
    addrs = {'127.0.0.1', 'localhost', '172.17.0.1'}
    try:
        out = subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=5).stdout
        addrs.update(out.split())
    except Exception:
        pass
    return addrs


def _local_endpoint_moved(configured, expected):
    configured_host, expected_host = _host_of(configured), _host_of(expected)
    if not configured_host or not expected_host or configured_host == expected_host:
        return False
    here = _this_host_addresses()
    return not (configured_host in here and expected_host in here)


def _local_client_id_wrong():
    # Only a backend- id is trusted with the master's site-scoped topics; an old
    # bridge-<id> is narrowed to devices/<id>/... and never gets guidance.
    device_id = get_device_id()
    client_id = get_configured_client_id()
    return bool(device_id and client_id) and client_id != f'backend-bridge-{device_id}'


def bridge_address_stale() -> bool:
    """True once the device has switched cloud, master or identity but the bridge has not followed."""
    expected = expected_bridge_address()
    configured = get_configured_bridge_address()
    if _is_local_bridge():
        return _local_endpoint_moved(configured, expected) or _local_client_id_wrong()
    if not expected:
        return False
    return bool(configured) and configured != expected


def update_bridge_config(token: str, device_id: str = None) -> dict:
    """
    Update VerneMQ bridge configuration with new token.

    VerneMQ bridge settings can be updated via vmq-admin or by
    restarting the container with new environment variables.

    For now, we restart the container with updated config.
    """
    try:
        # Bridge config file path (mounted into container)
        config_path = "/root/flex-run/setup/mqtt/vernemq-local.conf"

        if not os.path.exists(config_path):
            return {"success": False, "error": f"Config file not found: {config_path}"}

        # Read current config
        with open(config_path, 'r') as f:
            config = f.read()

        # Local-cloud (tcp bridge) trusts the backend- client_id with no token,
        # so there's nothing to inject and we must not rewrite the client_id.
        if 'vmq_bridge.tcp.' in config:
            return {"success": True, "skipped": "local-cloud tcp bridge (no token needed)"}

        # Update the password line (transport is ssl for cloud, tcp for local-cloud)
        new_config = re.sub(
            r'(vmq_bridge\.(?:ssl|tcp)\.gke\.password)\s*=\s*.*',
            lambda m: f'{m.group(1)} = {token}',
            config
        )

        # Optionally update client_id to include device_id
        if device_id:
            new_config = re.sub(
                r'(vmq_bridge\.(?:ssl|tcp)\.gke\.client_id)\s*=\s*.*',
                lambda m: f'{m.group(1)} = bridge-{device_id}',
                new_config
            )

        # Follow the cloud: the token is for the broker cloud_domain names
        address = expected_bridge_address()
        if address:
            new_config = re.sub(
                r'^(vmq_bridge\.ssl\.gke)\s*=\s*\S+[ \t]*$',
                lambda m: f'{m.group(1)} = {address}',
                new_config,
                flags=re.M
            )

        # Write updated config
        with open(config_path, 'w') as f:
            f.write(new_config)

        log.info(f"Updated bridge config with new token")
        return {"success": True, "config_updated": True}

    except Exception as e:
        log.error(f"Failed to update bridge config: {e}")
        return {"success": False, "error": str(e)}


def update_local_bridge_config() -> dict:
    """Point a local-cloud bridge at the current master under this device's id."""
    config_path = "/root/flex-run/setup/mqtt/vernemq-local.conf"
    try:
        with open(config_path) as f:
            config = f.read()

        new_config = config
        expected = expected_bridge_address()
        if _local_endpoint_moved(get_configured_bridge_address(), expected):
            new_config = re.sub(
                r'^(vmq_bridge\.tcp\.gke)\s*=\s*\S+[ \t]*$',
                lambda m: f'{m.group(1)} = {expected}',
                new_config,
                flags=re.M
            )

        device_id = get_device_id()
        if _local_client_id_wrong():
            new_config = re.sub(
                r'^(vmq_bridge\.tcp\.gke\.client_id)\s*=\s*\S+[ \t]*$',
                lambda m: f'{m.group(1)} = backend-bridge-{device_id}',
                new_config,
                flags=re.M
            )

        if new_config != config:
            with open(config_path, 'w') as f:
                f.write(new_config)
            log.info("Updated local-cloud bridge config")
        return {"success": True, "config_updated": new_config != config}

    except Exception as e:
        log.error(f"Failed to update local bridge config: {e}")
        return {"success": False, "error": str(e)}


def restart_vernemq() -> dict:
    """Restart VerneMQ container to apply new config"""
    try:
        result = subprocess.run(
            ["docker", "restart", VERNEMQ_CONTAINER],
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode == 0:
            log.info("VerneMQ container restarted successfully")
            return {"success": True, "message": "VerneMQ restarted"}
        else:
            log.error(f"Failed to restart VerneMQ: {result.stderr}")
            return {"success": False, "error": result.stderr}

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Restart timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_bridge_status() -> dict:
    """Get VerneMQ bridge connection status"""
    try:
        result = subprocess.run(
            ["docker", "exec", VERNEMQ_CONTAINER, "/vernemq/bin/vmq-admin", "bridge", "show"],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            return {"success": True, "status": result.stdout}
        else:
            return {"success": False, "error": result.stderr}

    except Exception as e:
        return {"success": False, "error": str(e)}


def get_bridge_metrics() -> dict:
    """Get VerneMQ bridge metrics to check connectivity"""
    try:
        result = subprocess.run(
            ["docker", "exec", VERNEMQ_CONTAINER, "/vernemq/bin/vmq-admin", "metrics", "show"],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            metrics = {}
            for line in result.stdout.split('\n'):
                if 'bridge' in line.lower():
                    parts = line.split('=')
                    if len(parts) == 2:
                        key = parts[0].strip()
                        try:
                            value = int(parts[1].strip())
                        except ValueError:
                            value = parts[1].strip()
                        metrics[key] = value
            return {"success": True, "metrics": metrics}
        else:
            return {"success": False, "error": result.stderr}

    except Exception as e:
        return {"success": False, "error": str(e)}


def get_bridge_port() -> str:
    """Port of the configured bridge endpoint (443 cloud / 31883 local-cloud)."""
    try:
        config_path = "/root/flex-run/setup/mqtt/vernemq-local.conf"
        with open(config_path) as f:
            for line in f:
                m = re.match(r'\s*vmq_bridge\.(?:ssl|tcp)\.gke\s*=\s*\S+:(\d+)', line)
                if m:
                    return m.group(1)
    except Exception as e:
        log.warning(f"Failed to read bridge port: {e}")
    return "443"


def _is_local_bridge() -> bool:
    """True for a local-cloud tcp bridge (no token to refresh, must not restart)."""
    try:
        with open("/root/flex-run/setup/mqtt/vernemq-local.conf") as f:
            return 'vmq_bridge.tcp.' in f.read()
    except Exception:
        return False


def _has_placeholder_credentials() -> bool:
    """True while the cloud bridge still carries build.sh's placeholder password."""
    try:
        with open("/root/flex-run/setup/mqtt/vernemq-local.conf") as f:
            for line in f:
                m = re.match(r'\s*vmq_bridge\.ssl\.gke\.password\s*=\s*(\S*)', line)
                if m:
                    return m.group(1) in PLACEHOLDER_PASSWORDS
    except Exception:
        pass
    return False


def check_tcp_connection_to_cloud() -> bool:
    """Check for an established TCP connection to the configured bridge endpoint."""
    port = get_bridge_port()
    try:
        result = subprocess.run(
            ["docker", "exec", VERNEMQ_CONTAINER, "netstat", "-tnp"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if f':{port}' in line and 'ESTABLISHED' in line and 'beam' in line:
                    return True
        return False
    except Exception as e:
        log.warning(f"Failed to check TCP connection: {e}")
        return False


def _proc_net_tcp_address(ip, port):
    return '%08X:%04X' % (struct.unpack('=I', socket.inet_aton(ip))[0], port)


def _bridge_socket_established(endpoint):
    """True/False for an ESTABLISHED socket from VerneMQ to the endpoint, None if it cannot be read."""
    try:
        host, port = endpoint.rsplit(':', 1)
        want = _proc_net_tcp_address(socket.gethostbyname(host), int(port))
        pid = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Pid}}", VERNEMQ_CONTAINER],
            capture_output=True, text=True, timeout=10
        ).stdout.strip()
        if not pid or pid == '0':
            return None
        with open(f'/proc/{pid}/net/tcp') as f:
            next(f, None)
            for line in f:
                parts = line.split()
                if len(parts) > 3 and parts[2] == want and parts[3] == '01':
                    return True
        return False
    except Exception as e:
        log.warning(f"Failed to read bridge socket state: {e}")
        return None


def is_bridge_healthy() -> tuple:
    """
    Check if the bridge is healthy by verifying connection state.

    Returns:
        (is_healthy: bool, reason: str)
    """
    global _last_publish_count, _last_check_time

    try:
        # First, check if bridge is configured
        status_result = get_bridge_status()
        if not status_result.get("success"):
            return False, f"Failed to get bridge status: {status_result.get('error')}"

        status_output = status_result.get("status", "")

        # Check if bridge 'gke' is in the output (means it's configured)
        if 'gke' not in status_output.lower():
            return False, "Bridge 'gke' not configured"

        if _is_local_bridge():
            endpoint = get_configured_bridge_address()
            established = _bridge_socket_established(endpoint) if endpoint else None
            if established is None:
                return True, "Bridge configured (local-cloud tcp)"
            if established:
                return True, f"Bridge connected to {endpoint}"
            return False, f"No connection to local-cloud broker {endpoint}"

        # The cloud broker holds the socket open even for a bad password, so
        # the TCP probe alone would call this healthy and never inject the token.
        if _has_placeholder_credentials():
            return False, "Bridge using placeholder credentials"

        # Check for actual TCP connection to the bridge endpoint
        has_tcp_conn = check_tcp_connection_to_cloud()
        if not has_tcp_conn:
            return False, f"No TCP connection to bridge endpoint (port {get_bridge_port()})"

        # Secondary check: monitor message flow for additional health info
        metrics_result = get_bridge_metrics()
        if metrics_result.get("success"):
            metrics = metrics_result.get("metrics", {})
            publish_in = metrics.get("counter.gke_vmq_bridge_publish_in_0", 0)
            publish_out = metrics.get("counter.gke_vmq_bridge_publish_out_0", 0)
            current_count = publish_in + publish_out

            current_time = time.time()

            # First check - just record baseline
            if _last_check_time == 0:
                _last_publish_count = current_count
                _last_check_time = current_time
                return True, "Bridge connected (initial check)"

            count_diff = current_count - _last_publish_count
            time_diff = current_time - _last_check_time

            # Update for next check
            _last_publish_count = current_count
            _last_check_time = current_time

            return True, f"Bridge connected: {count_diff} msgs in {int(time_diff)}s"

        return True, "Bridge connected (metrics unavailable)"

    except Exception as e:
        return False, f"Health check error: {e}"


def _do_local_bridge_refresh():
    config_result = update_local_bridge_config()
    if not config_result.get("success"):
        log.error(f"[Bridge Health] Local config update failed: {config_result.get('error')}")
        return False

    restart_result = restart_vernemq()
    if not restart_result.get("success"):
        log.error(f"[Bridge Health] VerneMQ restart failed: {restart_result.get('error')}")
        return False

    log.info("[Bridge Health] Local-cloud bridge refreshed")
    return True


def _do_bridge_refresh():
    """Perform bridge refresh (update config and restart VerneMQ)"""
    if _is_local_bridge():
        return _do_local_bridge_refresh()

    token = get_access_token()
    if not token:
        log.warning("[Bridge Health] No access token, cannot refresh")
        return False

    device_id = get_device_id()

    config_result = update_bridge_config(token, device_id)
    if not config_result.get("success"):
        log.error(f"[Bridge Health] Config update failed: {config_result.get('error')}")
        return False

    restart_result = restart_vernemq()
    if not restart_result.get("success"):
        log.error(f"[Bridge Health] VerneMQ restart failed: {restart_result.get('error')}")
        return False

    log.info("[Bridge Health] Bridge refreshed successfully")
    return True


def _health_monitor_loop():
    """Background thread that monitors bridge health"""
    global _health_monitor_running, _last_check_time, _last_publish_count
    global _local_refresh_backoff, _local_refresh_not_before

    log.info("[Bridge Health] Monitor started")

    # Reset state
    _last_check_time = 0
    _last_publish_count = 0
    _local_refresh_backoff = 0
    _local_refresh_not_before = 0.0

    consecutive_failures = 0
    max_failures_before_refresh = 2  # Refresh after 2 consecutive failures

    if _has_placeholder_credentials():
        log.warning("[Bridge Health] Placeholder bridge credentials, injecting token")
        _do_bridge_refresh()

    while _health_monitor_running:
        try:
            time.sleep(HEALTH_CHECK_INTERVAL)

            if not _health_monitor_running:
                break

            # A bridge to the old cloud stays connected, so health alone never moves it
            if bridge_address_stale():
                log.warning(f"[Bridge Health] Bridge stale, moving from "
                            f"{get_configured_bridge_address()} ({get_configured_client_id()}) "
                            f"to {expected_bridge_address()}")
                if _do_bridge_refresh():
                    consecutive_failures = 0
                    _last_check_time = 0
                    _last_publish_count = 0
                    time.sleep(15)
                continue

            is_healthy, reason = is_bridge_healthy()

            if is_healthy:
                if consecutive_failures > 0:
                    log.info(f"[Bridge Health] Recovered: {reason}")
                consecutive_failures = 0
                _local_refresh_backoff = 0
                _local_refresh_not_before = 0.0
                log.debug(f"[Bridge Health] OK: {reason}")
            else:
                consecutive_failures += 1
                log.error(f"[Bridge Health] DISCONNECTED ({consecutive_failures}/{max_failures_before_refresh}): {reason}")

                if consecutive_failures >= max_failures_before_refresh:
                    if _is_local_bridge():
                        now = time.monotonic()
                        if now < _local_refresh_not_before:
                            continue
                        _local_refresh_backoff = min(
                            max(_local_refresh_backoff * 2, HEALTH_CHECK_INTERVAL * 2),
                            LOCAL_REFRESH_BACKOFF_MAX)
                        _local_refresh_not_before = now + _local_refresh_backoff
                    log.warning("[Bridge Health] Triggering auto-refresh")
                    if _do_bridge_refresh():
                        consecutive_failures = 0
                        # Reset metrics tracking after refresh
                        _last_check_time = 0
                        _last_publish_count = 0
                        # Wait for VerneMQ to fully restart
                        time.sleep(15)

        except Exception as e:
            log.error(f"[Bridge Health] Monitor error: {e}")

    log.info("[Bridge Health] Monitor stopped")


def start_health_monitor():
    """Start the bridge health monitor background thread"""
    global _health_monitor_thread, _health_monitor_running

    if _health_monitor_running:
        log.warning("[Bridge Health] Monitor already running")
        return

    _health_monitor_running = True
    _health_monitor_thread = threading.Thread(target=_health_monitor_loop, daemon=True)
    _health_monitor_thread.start()
    log.info("[Bridge Health] Monitor thread started")


def stop_health_monitor():
    """Stop the bridge health monitor"""
    global _health_monitor_running

    _health_monitor_running = False
    log.info("[Bridge Health] Monitor stopping...")


class BridgeStatus(Resource):
    def get(self):
        """Get current bridge connection status"""
        status = get_bridge_status()
        token = get_access_token()
        device_id = get_device_id()

        return {
            "bridge": status,
            "has_token": token is not None,
            "device_id": device_id
        }


class BridgeRefresh(Resource):
    """Refresh VerneMQ bridge credentials"""

    @auth.requires_auth
    def post(self):
        """
        Update bridge with current access token and restart.

        Call this after token refresh to update bridge credentials.
        """
        token = get_access_token()
        if not token:
            return {"success": False, "error": "No access token found"}, 404

        device_id = get_device_id()

        # Update config with new token
        config_result = update_bridge_config(token, device_id)
        if not config_result.get("success"):
            return config_result, 500

        # Restart VerneMQ to apply new config
        restart_result = restart_vernemq()
        if not restart_result.get("success"):
            return restart_result, 500

        return {
            "success": True,
            "message": "Bridge credentials updated and VerneMQ restarted",
            "device_id": device_id
        }


class BridgeToken(Resource):
    """Manually set bridge token (for testing)"""

    @auth.requires_auth
    def post(self):
        """Set a specific token for the bridge"""
        data = request.json or {}
        token = data.get('token')

        if not token:
            return {"success": False, "error": "No token provided"}, 400

        device_id = get_device_id()

        config_result = update_bridge_config(token, device_id)
        if not config_result.get("success"):
            return config_result, 500

        # Optionally restart
        if data.get('restart', True):
            restart_result = restart_vernemq()
            return {**config_result, **restart_result}

        return config_result


class BridgeHealth(Resource):
    """Check bridge health status"""

    @auth.requires_auth
    def get(self):
        """Get bridge health status and metrics"""
        is_healthy, reason = is_bridge_healthy()
        metrics_result = get_bridge_metrics()

        return {
            "healthy": is_healthy,
            "reason": reason,
            "metrics": metrics_result.get("metrics", {}),
            "bridge_address": get_configured_bridge_address(),
            "expected_bridge_address": expected_bridge_address(),
            "monitor_running": _health_monitor_running,
            "check_interval": HEALTH_CHECK_INTERVAL
        }


class BridgeMonitor(Resource):
    """Control bridge health monitor"""

    @auth.requires_auth
    def post(self):
        """Start or stop the health monitor"""
        data = request.json or {}
        action = data.get('action', 'start')

        if action == 'start':
            start_health_monitor()
            return {"success": True, "message": "Health monitor started"}
        elif action == 'stop':
            stop_health_monitor()
            return {"success": True, "message": "Health monitor stopped"}
        else:
            return {"success": False, "error": f"Unknown action: {action}"}, 400


def register_routes(api):
    """Register MQTT bridge management routes and start health monitor"""
    api.add_resource(BridgeStatus, '/mqtt/bridge/status')
    api.add_resource(BridgeRefresh, '/mqtt/bridge/refresh')
    api.add_resource(BridgeToken, '/mqtt/bridge/token')
    api.add_resource(BridgeHealth, '/mqtt/bridge/health')
    api.add_resource(BridgeMonitor, '/mqtt/bridge/monitor')

    # Auto-start health monitor
    start_health_monitor()
