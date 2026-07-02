#!/bin/bash
# Deploy the assembly-guidance frontend (assembly-client container).
# Invoked by the "Enable Assembly Guidance" toggle via the system_server job queue
# (worker_scripts/job_manager.py -> enable_assembly_guidance).

# Arch-aware image name, matching the on-prem convention: fvonprem/<arch>-<component>:<tag>
ARCH=$(uname -m | sed 's/x86_64/x86/; s/aarch64/arm/')
IMAGE_TAG="${IMAGE_TAG:-1}"   # matches the pushed image tag; override with IMAGE_TAG=...
IMAGE_NAME="fvonprem/${ARCH}-assembly-client:${IMAGE_TAG}"

echo "Deploying assembly-guidance: ${IMAGE_NAME}"
docker pull "$IMAGE_NAME" || echo "Warning: pull failed — using local image if present"

docker stop assembly-client 2>/dev/null
docker rm assembly-client 2>/dev/null

docker run -d \
    --name=assembly-client \
    --restart unless-stopped \
    -p 3021:3021 \
    -v /home/visioncell/Documents:/Documents \
    --log-opt max-size=50m --log-opt max-file=5 \
    "$IMAGE_NAME"
