#!/bin/sh
# Deploy the anomaly inference container.
#   install_anomaly.sh [host_models_dir]
#
# Idempotent: if the container already exists it is started, not recreated.
# Recreating means re-supplying every flag correctly, and getting one wrong
# (a missing mount, a lost port) fails in ways that look like a broken model.
set -e
MODELS_DIR="${1:-/anomaly/models}"
IMAGE="${ANOMALY_IMAGE:-fvonprem/x86-anomaly-server:v1}"
NAME=anomaly-server
PORT="${ANOMALY_PORT:-5703}"

mkdir -p "$MODELS_DIR"

if docker inspect "$NAME" >/dev/null 2>&1; then
    echo "$NAME already exists - starting it"
    docker start "$NAME"
    exit 0
fi

echo "pulling $IMAGE (this is a large image)"
docker pull "$IMAGE"

# --gpus first; the image runs on CPU roughly an order of magnitude slower, so a
# GPU-less host should still get a working service rather than a failed install.
echo "creating $NAME on port $PORT, models at $MODELS_DIR"
docker run -d --name "$NAME" \
    --restart unless-stopped \
    --log-opt max-size=50m --log-opt max-file=5 \
    -p "$PORT":8080 \
    -e SERVE_ONLY=1 -e MODELS_DIR=/models \
    -v "$MODELS_DIR":/models \
    --gpus device=0 \
    "$IMAGE" \
|| docker run -d --name "$NAME" \
    --restart unless-stopped \
    --log-opt max-size=50m --log-opt max-file=5 \
    -p "$PORT":8080 \
    -e SERVE_ONLY=1 -e MODELS_DIR=/models \
    -v "$MODELS_DIR":/models \
    "$IMAGE"
