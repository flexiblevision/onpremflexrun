#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Accept parameters: ARCH and TAG (for consistency with system_setup.sh)
SYSTEM_ARCH="${1:-x86}"
IMAGE_TAG="${2:-dev}"

# 'local'/'cloud' are deployment environs, not published image channels — map them
# to a real channel (override with VERNEMQ_TAG) so we don't pull a nonexistent tag.
case "$IMAGE_TAG" in
    local|cloud|"") IMAGE_TAG="${VERNEMQ_TAG:-dev}" ;;
esac

IMAGE_NAME="fvonprem/${SYSTEM_ARCH}-vernemq:${IMAGE_TAG}"
CONTAINER_NAME="vernemq"

# Config lives next to this script. Override with CONFIG_FILE=... if needed.
# Note: system_server (mqtt_routes.py) edits the copy at /root/flex-run/setup/mqtt/
# at runtime, so in production this resolves to that same path.
CONFIG_FILE="${CONFIG_FILE:-$SCRIPT_DIR/vernemq-local.conf}"

echo "Setting up MQTT (VerneMQ)..."
echo "  Image: ${IMAGE_NAME}"

# Generate the runtime config if it's missing (fresh box). build.sh honors any
# BRIDGE_* env vars that are set, and falls back to a placeholder password that
# system_server (mqtt_routes.py) replaces with the real token at runtime.
if [ ! -f "$CONFIG_FILE" ]; then
    if [ -x "$SCRIPT_DIR/build.sh" ] || [ -f "$SCRIPT_DIR/build.sh" ]; then
        echo "Config not found — generating it with build.sh..."
        CONFIG_FILE="$CONFIG_FILE" sh "$SCRIPT_DIR/build.sh" --allow-placeholder
    else
        echo "ERROR: Runtime config not found at $CONFIG_FILE"
        echo "Run build.sh first to generate the config"
        exit 1
    fi
fi

# Stop and remove existing container if present
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "Removing existing container..."
    docker rm -f "$CONTAINER_NAME"
fi

# Read bridge settings from config file
BRIDGE_ADDR=$(grep "vmq_bridge.ssl.gke =" "$CONFIG_FILE" | cut -d= -f2 | tr -d ' ')
BRIDGE_CLIENT_ID=$(grep "vmq_bridge.ssl.gke.client_id" "$CONFIG_FILE" | cut -d= -f2 | tr -d ' ')
BRIDGE_USERNAME=$(grep "vmq_bridge.ssl.gke.username" "$CONFIG_FILE" | cut -d= -f2 | tr -d ' ')
BRIDGE_PASSWORD=$(grep "vmq_bridge.ssl.gke.password" "$CONFIG_FILE" | cut -d= -f2 | tr -d ' ')

## Bridge and topic config is in local.conf (mounted below) - do NOT duplicate via env vars
## Env vars override conf files and cause duplicate subscriptions if both are set
docker run -d \
    --name "$CONTAINER_NAME" \
    --restart unless-stopped \
    --network host \
    --log-opt max-size=50m \
    --log-opt max-file=5 \
    -v "$CONFIG_FILE:/vernemq/etc/conf.d/local.conf:ro" \
    "$IMAGE_NAME"

echo "MQTT broker started on localhost:1883"
echo "Config mounted from: $CONFIG_FILE"
