#!/bin/bash
set -e

# Generates the runtime VerneMQ config (vernemq-local.conf) consumed by
# setup_mqtt.sh and edited at runtime by system_server's mqtt_routes.py.
#
# The generated file is gitignored because it carries the bridge credential.
# Static config (listener, webhooks, topic map, TLS) is baked in here; only the
# bridge identity is parameterized.
#
# Usage:
#   BRIDGE_PASSWORD=secret ./build.sh
#   BRIDGE_ADDRESS=mqtt.flexiblevision.com:443 BRIDGE_PASSWORD=secret ./build.sh
#
# Inputs (environment variables):
#   BRIDGE_ADDRESS    bridge endpoint host:port   (default: mqtt-dev.flexiblevision.com:443)
#   BRIDGE_USERNAME   bridge username             (default: bridge)
#   BRIDGE_CLIENT_ID  bridge client id            (default: local-bridge)
#   BRIDGE_PASSWORD   bridge password/token       (required unless --allow-placeholder)
#
# Flags:
#   --allow-placeholder   write a placeholder password instead of failing
#                         (useful for first-time scaffolding; the real value is
#                         injected later by mqtt_routes.py on token refresh)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Output path — overridable so setup_mqtt.sh can keep both in sync.
CONFIG_FILE="${CONFIG_FILE:-$SCRIPT_DIR/vernemq-local.conf}"

ALLOW_PLACEHOLDER=0
for arg in "$@"; do
    case "$arg" in
        --allow-placeholder) ALLOW_PLACEHOLDER=1 ;;
    esac
done

# Default bridge endpoint depends on deployment mode (read from ~/fvconfig.json):
#   environ=local (local-cloud) -> in-cluster VerneMQ NodePort, plain TCP
#   otherwise     (cloud)       -> public broker, TLS on :443
ENVIRON_CFG="$(jq -r '.environ' "$HOME/fvconfig.json" 2>/dev/null)"
CLOUD_DOMAIN_CFG="$(jq -r '.cloud_domain' "$HOME/fvconfig.json" 2>/dev/null)"
CLUSTER_MQTT_NODEPORT="${CLUSTER_MQTT_NODEPORT:-31883}"

# Resolve a unique device id for the bridge client_id (fleet-safe, no manual input):
# fvconfig -> edge Mongo (same source system_server uses) -> NIC MAC -> hostname.
DEVICE_ID_CFG="$(jq -r '.device_id // empty' "$HOME/fvconfig.json" 2>/dev/null)"
if [ -z "$DEVICE_ID_CFG" ]; then
    DEVICE_ID_CFG="$(docker exec mongo mongo fvonprem --quiet --eval 'var x=db.utils.findOne({type:"device_id"}); if (x && x.id) print(x.id)' 2>/dev/null | tr -d '\r\n ')"
fi
if [ -z "$DEVICE_ID_CFG" ]; then
    _iface="$(jq -r '.interface_name // empty' "$HOME/fvconfig.json" 2>/dev/null)"
    [ -n "$_iface" ] && DEVICE_ID_CFG="$(cat "/sys/class/net/$_iface/address" 2>/dev/null | tr -d ':')"
fi
[ -z "$DEVICE_ID_CFG" ] && DEVICE_ID_CFG="$(hostname)"
if [ "$ENVIRON_CFG" = "local" ]; then
    # Bridge target = the cloud master set at registration (server_ip -> cloud_domain).
    CLUSTER_HOST="$(printf '%s' "$CLOUD_DOMAIN_CFG" | sed -e 's#^[a-zA-Z]*://##' -e 's#/.*##' -e 's#:.*##')"
    if [ -z "$CLUSTER_HOST" ] || [ "$CLUSTER_HOST" = "null" ]; then CLUSTER_HOST="127.0.0.1"; fi
    case "$CLUSTER_HOST" in
        localhost|127.0.0.1)
            echo "WARNING: cloud_domain='$CLOUD_DOMAIN_CFG' -> bridge would target localhost, which"
            echo "         usually can't reach the cloud cluster. Register the device with its server_ip"
            echo "         (sets cloud_domain), or pass BRIDGE_ADDRESS=<server_ip>:${CLUSTER_MQTT_NODEPORT}." ;;
    esac
    DEFAULT_BRIDGE_ADDRESS="${CLUSTER_HOST}:${CLUSTER_MQTT_NODEPORT}"
    # The in-cluster broker trusts backend-* client ids without a token, so no
    # BRIDGE_PASSWORD is needed in local-cloud mode.
    DEFAULT_CLIENT_ID="backend-bridge-${DEVICE_ID_CFG}"
    ALLOW_PLACEHOLDER=1
else
    DEFAULT_BRIDGE_ADDRESS="mqtt-dev.flexiblevision.com:443"
    DEFAULT_CLIENT_ID="local-bridge"
fi

# Bridge identity — overridable via environment, with sane defaults.
BRIDGE_ADDRESS="${BRIDGE_ADDRESS:-$DEFAULT_BRIDGE_ADDRESS}"
BRIDGE_USERNAME="${BRIDGE_USERNAME:-bridge}"
BRIDGE_CLIENT_ID="${BRIDGE_CLIENT_ID:-$DEFAULT_CLIENT_ID}"

# Transport: TLS for a :443 cloud endpoint, plain TCP for an intranet/local-cloud
# broker. Override with BRIDGE_TRANSPORT=ssl|tcp.
case "$BRIDGE_ADDRESS" in
    *:443) _default_transport="ssl" ;;
    *)     _default_transport="tcp" ;;
esac
BRIDGE_TRANSPORT="${BRIDGE_TRANSPORT:-$_default_transport}"
BRIDGE_KEY="vmq_bridge.${BRIDGE_TRANSPORT}.gke"

if [ "$BRIDGE_TRANSPORT" = "ssl" ]; then
    TLS_BLOCK="${BRIDGE_KEY}.insecure = on
${BRIDGE_KEY}.tls_version = tlsv1.2"
else
    TLS_BLOCK=""
fi

if [ -z "${BRIDGE_PASSWORD:-}" ]; then
    if [ "$ALLOW_PLACEHOLDER" -eq 1 ]; then
        BRIDGE_PASSWORD="your-bridge-secret"
        if [ "$ENVIRON_CFG" = "local" ]; then
            echo "Local-cloud mode: no token needed (broker trusts client_id '${BRIDGE_CLIENT_ID}')."
        else
            echo "WARNING: BRIDGE_PASSWORD not set — writing placeholder."
            echo "         The bridge will not authenticate until a real token is written"
            echo "         (set BRIDGE_PASSWORD and re-run, or let system_server inject it)."
        fi
    else
        echo "ERROR: BRIDGE_PASSWORD is not set."
        echo "Set it before running:  BRIDGE_PASSWORD=<token> $0"
        echo "Or scaffold a placeholder:  $0 --allow-placeholder"
        exit 1
    fi
fi

echo "Generating VerneMQ config..."
echo "  Output:    $CONFIG_FILE"
echo "  Endpoint:  $BRIDGE_ADDRESS"
echo "  Transport: $BRIDGE_TRANSPORT"
echo "  Username:  $BRIDGE_USERNAME"
echo "  ClientID:  $BRIDGE_CLIENT_ID"
echo "  Password:  $(printf '%s' "$BRIDGE_PASSWORD" | cut -c1-2)***"

cat > "$CONFIG_FILE" <<EOF
## Basic listener config
listener.tcp.default = 0.0.0.0:1883
allow_anonymous = off

## Webhook plugin configuration
plugins.vmq_webhooks = on

vmq_webhooks.mywebhook1.hook = auth_on_register
vmq_webhooks.mywebhook1.endpoint = http://172.17.0.1:5000/api/capture/mqtt/auth

vmq_webhooks.mywebhook2.hook = auth_on_subscribe
vmq_webhooks.mywebhook2.endpoint = http://172.17.0.1:5000/api/capture/mqtt/subscribe

vmq_webhooks.mywebhook3.hook = auth_on_publish
vmq_webhooks.mywebhook3.endpoint = http://172.17.0.1:5000/api/capture/mqtt/publish

## Bridge to upstream VerneMQ (${BRIDGE_TRANSPORT} -> ${BRIDGE_ADDRESS})
## Generated by build.sh — edit BRIDGE_* env vars and re-run rather than hand-editing.
plugins.vmq_bridge = on

${BRIDGE_KEY} = ${BRIDGE_ADDRESS}
${BRIDGE_KEY}.client_id = ${BRIDGE_CLIENT_ID}
${BRIDGE_KEY}.username = ${BRIDGE_USERNAME}
${BRIDGE_KEY}.password = ${BRIDGE_PASSWORD}
${TLS_BLOCK}
${BRIDGE_KEY}.cleansession = on


## Outbound: device → cloud
## NOTE: VerneMQ cuttlefish parser strips '#' from bridge topics.
## Use explicit subtopic patterns instead of '#' wildcards.
${BRIDGE_KEY}.topic.1 = devices/+/system/sync out 0
${BRIDGE_KEY}.topic.2 = devices/+/assembly/+/started out 0
${BRIDGE_KEY}.topic.3 = devices/+/assembly/+/workstation/started out 0
${BRIDGE_KEY}.topic.4 = devices/+/assembly/+/workstation/completed out 0
${BRIDGE_KEY}.topic.5 = devices/+/assembly/+/workstation/skipped out 0
${BRIDGE_KEY}.topic.6 = devices/+/assembly/+/step/recorded out 0
${BRIDGE_KEY}.topic.7 = devices/+/assembly/+/status out 0
${BRIDGE_KEY}.topic.8 = devices/+/assembly/+/line/transition out 0
${BRIDGE_KEY}.topic.9 = devices/+/assembly/+/logs out 0
${BRIDGE_KEY}.topic.10 = devices/+/assembly/+/metrics out 0
${BRIDGE_KEY}.topic.11 = devices/+/stats/+ out 0
${BRIDGE_KEY}.topic.12 = devices/+/heartbeat out 0

## Inbound: cloud → device (commands)
${BRIDGE_KEY}.topic.13 = devices/+/system/reboot in 0
${BRIDGE_KEY}.topic.14 = devices/+/system/shutdown in 0
${BRIDGE_KEY}.topic.15 = devices/+/system/refresh_backend in 0
${BRIDGE_KEY}.topic.16 = devices/+/system/node_trigger in 0
${BRIDGE_KEY}.topic.17 = devices/+/system/toggle_sync in 0
${BRIDGE_KEY}.topic.18 = devices/+/system/update_software in 0
${BRIDGE_KEY}.topic.19 = devices/+/system/start_teamviewer in 0
${BRIDGE_KEY}.topic.20 = devices/+/models in 0
${BRIDGE_KEY}.topic.21 = devices/+/preset/+ in 0
${BRIDGE_KEY}.topic.22 = devices/+/timemachine/+ in 0
EOF

# 644, not 600: the file is bind-mounted into the VerneMQ container, which runs
# as a non-root user and must be able to read it. 600 (root-only) makes cuttlefish
# fail to open it at startup.
chmod 644 "$CONFIG_FILE"

echo "Done. Wrote $(wc -l < "$CONFIG_FILE") lines to $CONFIG_FILE"
echo "Next: run ./setup_mqtt.sh to (re)start the broker with this config."
