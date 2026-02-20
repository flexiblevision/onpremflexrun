"""
MQTT Bridge Management Routes

Manages the VerneMQ bridge connection to cloud MQTT broker.
Updates bridge credentials when access token refreshes.
Includes health monitoring to auto-reconnect if bridge disconnects.
"""

import os
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

# MongoDB connection for reading tokens
client = MongoClient("172.17.0.1")
utils_db = client["fvonprem"]["utils"]

# VerneMQ container name
VERNEMQ_CONTAINER = "vernemq"


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

        # Update the password line
        import re
        new_config = re.sub(
            r'vmq_bridge\.ssl\.gke\.password\s*=\s*.*',
            f'vmq_bridge.ssl.gke.password = {token}',
            config
        )

        # Optionally update client_id to include device_id
        if device_id:
            new_config = re.sub(
                r'vmq_bridge\.ssl\.gke\.client_id\s*=\s*.*',
                f'vmq_bridge.ssl.gke.client_id = bridge-{device_id}',
                new_config
            )

        # Write updated config
        with open(config_path, 'w') as f:
            f.write(new_config)

        log.info(f"Updated bridge config with new token")
        return {"success": True, "config_updated": True}

    except Exception as e:
        log.error(f"Failed to update bridge config: {e}")
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


def check_tcp_connection_to_cloud() -> bool:
    """Check if there's an established TCP connection to cloud VerneMQ (port 443)"""
    try:
        # Check for TCP connections from within the vernemq container
        result = subprocess.run(
            ["docker", "exec", VERNEMQ_CONTAINER, "netstat", "-tnp"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            # Look for ESTABLISHED connections to port 443 from beam.smp (VerneMQ)
            for line in result.stdout.split('\n'):
                if ':443' in line and 'ESTABLISHED' in line and 'beam' in line:
                    return True
        return False
    except Exception as e:
        log.warning(f"Failed to check TCP connection: {e}")
        return False


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

        # Check for actual TCP connection to cloud VerneMQ
        has_tcp_conn = check_tcp_connection_to_cloud()
        if not has_tcp_conn:
            return False, "No TCP connection to cloud VerneMQ (port 443)"

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


def _do_bridge_refresh():
    """Perform bridge refresh (update config and restart VerneMQ)"""
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

    log.info("[Bridge Health] Monitor started")

    # Reset state
    _last_check_time = 0
    _last_publish_count = 0

    consecutive_failures = 0
    max_failures_before_refresh = 2  # Refresh after 2 consecutive failures

    while _health_monitor_running:
        try:
            time.sleep(HEALTH_CHECK_INTERVAL)

            if not _health_monitor_running:
                break

            is_healthy, reason = is_bridge_healthy()

            if is_healthy:
                if consecutive_failures > 0:
                    log.info(f"[Bridge Health] Recovered: {reason}")
                consecutive_failures = 0
                log.debug(f"[Bridge Health] OK: {reason}")
            else:
                consecutive_failures += 1
                log.error(f"[Bridge Health] DISCONNECTED ({consecutive_failures}/{max_failures_before_refresh}): {reason}")

                if consecutive_failures >= max_failures_before_refresh:
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
