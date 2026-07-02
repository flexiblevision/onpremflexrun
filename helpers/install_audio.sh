#!/bin/bash
# Deploy the audio-devices service (audio-anomaly container).
# Invoked by the "Enable Audio Devices" toggle via the system_server job queue
# (worker_scripts/job_manager.py -> enable_audio).
#
# Source repo: FVKWS/audio_anomaly (Flask API :5702 + device WebSocket :5701).
# Uses the GPU on-demand (--gpus device=0, shared with other services; no memory
# reserved up front, CPU fallback if unavailable); devices/baselines persist to a
# mounted data volume; MongoDB is reached over the host network.

ARCH=$(uname -m | sed 's/x86_64/x86/; s/aarch64/arm/')
IMAGE_TAG="${IMAGE_TAG:-1}"   # matches the pushed image tag; override with IMAGE_TAG=...
IMAGE_NAME="fvonprem/${ARCH}-audio-anomaly:${IMAGE_TAG}"
DATA_DIR="/home/visioncell/Documents/audio_anomaly_data"

mkdir -p "$DATA_DIR"

echo "Deploying audio-devices service: ${IMAGE_NAME}"
docker pull "$IMAGE_NAME" || echo "Warning: pull failed — using local image if present"

docker stop audio-anomaly 2>/dev/null
docker rm audio-anomaly 2>/dev/null

docker run -d \
    --name=audio-anomaly \
    --restart unless-stopped \
    --network host \
    --gpus device=0 \
    -e MONGO_URI=mongodb://172.17.0.1:27017/ \
    -v "$DATA_DIR:/app/data" \
    --log-opt max-size=50m --log-opt max-file=5 \
    "$IMAGE_NAME"
