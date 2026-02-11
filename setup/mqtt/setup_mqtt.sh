#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Accept parameters: ARCH and TAG (for consistency with system_setup.sh)
SYSTEM_ARCH="${1:-x86}"
IMAGE_TAG="${2:-dev}"

IMAGE_NAME="fvonprem/${SYSTEM_ARCH}-vernemq:${IMAGE_TAG}"
CONTAINER_NAME="vernemq"

# Use production path for config - must match what system_server expects
CONFIG_FILE="/root/flex-run/setup/mqtt/vernemq-local.conf"

echo "Setting up MQTT (VerneMQ)..."
echo "  Image: ${IMAGE_NAME}"

# Check that runtime config exists
if [ ! -f "$CONFIG_FILE" ]; then
    echo "ERROR: Runtime config not found at $CONFIG_FILE"
    echo "Run build.sh first to generate the config"
    exit 1
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

docker run -d \
    --name "$CONTAINER_NAME" \
    --restart unless-stopped \
    --network host \
    --log-opt max-size=50m \
    --log-opt max-file=5 \
    -v "$CONFIG_FILE:/vernemq/etc/conf.d/local.conf:ro" \
    -e "DOCKER_VERNEMQ_VMQ_BRIDGE__SSL__GKE=${BRIDGE_ADDR}" \
    -e "DOCKER_VERNEMQ_VMQ_BRIDGE__SSL__GKE__CLIENT_ID=${BRIDGE_CLIENT_ID}" \
    -e "DOCKER_VERNEMQ_VMQ_BRIDGE__SSL__GKE__USERNAME=${BRIDGE_USERNAME}" \
    -e "DOCKER_VERNEMQ_VMQ_BRIDGE__SSL__GKE__PASSWORD=${BRIDGE_PASSWORD}" \
    -e "DOCKER_VERNEMQ_VMQ_BRIDGE__SSL__GKE__INSECURE=on" \
    -e "DOCKER_VERNEMQ_VMQ_BRIDGE__SSL__GKE__TLS_VERSION=tlsv1.2" \
    -e "DOCKER_VERNEMQ_VMQ_BRIDGE__SSL__GKE__CLEANSESSION=on" \
    -e "DOCKER_VERNEMQ_VMQ_BRIDGE__SSL__GKE__TOPIC__1=* both 0" \
    -e "DOCKER_VERNEMQ_PLUGINS__VMQ_BRIDGE=on" \
    "$IMAGE_NAME"

echo "MQTT broker started on localhost:1883"
echo "Config mounted from: $CONFIG_FILE"
