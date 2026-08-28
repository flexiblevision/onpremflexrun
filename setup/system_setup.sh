#!/bin/sh
# First install: run every container for the first time on a new device.
#
# Ordered pull -> run -> verify rather than nine unchecked `docker run` calls.
# All images are pulled before anything starts, so a bad version fails before
# the device is half-built; every container is verified at the end, so an
# install cannot report success and leave a device that looks set up and does
# not work.
#
# Exit codes: 0 ok  20 bad arguments  21 bad config  22 pull failed
#            23 run failed  24 verification failed

set -eu

CAPDEV_VERSION="${1:-}"
CAPTUREUI_VERSION="${2:-}"
PREDICTION_VERSION="${3:-}"
SYSTEM_ARCH="${4:-}"
PREDICT_LITE_VERSION="${5:-}"
VISION_VERSION="${6:-}"
CREATOR_VERSION="${7:-}"
VISIONTOOLS_VERSION="${8:-}"

for _lib in "$(dirname "$0")/../upgrades/lib/deploy_common.sh" \
            "$HOME/flex-run/upgrades/lib/deploy_common.sh"; do
    if [ -r "$_lib" ]; then . "$_lib"; _lib_loaded=1; break; fi
done
if [ -z "${_lib_loaded:-}" ]; then
    echo "ERROR: cannot find upgrades/lib/deploy_common.sh - deploy tree is incomplete" >&2
    exit 20
fi

log()  { echo "[system_setup] $*"; }
fail() { echo "[system_setup] ERROR: $*" >&2; }

# --- arguments --------------------------------------------------------------
case "$SYSTEM_ARCH" in
    x86|arm) ;;
    *) fail "unsupported architecture '$SYSTEM_ARCH' - expected x86 or arm"; exit 20 ;;
esac

# visiontools has no arm image yet. Mirrors NOT_ON_ARCH in release/manifest.py -
# keep the two in step. The version check returns the string 'True' for a
# container it thinks needs nothing, which is not a tag either.
VISIONTOOLS_ENABLED=1
if [ "$SYSTEM_ARCH" = "arm" ] || [ "$VISIONTOOLS_VERSION" = "True" ]; then
    VISIONTOOLS_ENABLED=''
    log "skipping visiontools on $SYSTEM_ARCH (no image published)"
fi

# An empty version string becomes "fvonprem/x86-backend:", which pulls a tag
# that does not exist, so catching it here names the argument instead.
_required="capdev:$CAPDEV_VERSION captureui:$CAPTUREUI_VERSION
           prediction:$PREDICTION_VERSION predictlite:$PREDICT_LITE_VERSION
           vision:$VISION_VERSION nodecreator:$CREATOR_VERSION"
if [ -n "$VISIONTOOLS_ENABLED" ]; then
    _required="$_required visiontools:$VISIONTOOLS_VERSION"
fi

_missing=''
for _pair in $_required; do
    if [ -z "${_pair#*:}" ]; then
        _missing="$_missing ${_pair%%:*}"
    fi
done
if [ -n "$_missing" ]; then
    fail "missing version argument(s):$_missing"
    exit 20
fi

# --- config -----------------------------------------------------------------
# jq prints the string "null" for an absent key and exits 0, so an unchecked
# read here installs containers configured to authenticate against "null".
CONFIG="$HOME/fvconfig.json"
if [ ! -r "$CONFIG" ]; then
    fail "$CONFIG is missing or unreadable"
    exit 21
fi

read_config() {
    value="$(jq -r ".$1" "$CONFIG" 2>/dev/null || echo '')"
    case "$value" in
        ''|null) echo '' ;;
        *) echo "$value" ;;
    esac
}

AUTH0_DOMAIN="$(read_config auth0_domain)"
AUTH0_CID="$(read_config auth0_CID)"
AUTH0_ALGORITHMS="$(read_config auth_alg)"
JWT_SECRET="$(read_config jwt_secret_key)"
CLOUD_DOMAIN="$(read_config cloud_domain)"
GCP_FUNCTIONS_DOMAIN="$(read_config gcp_functions_domain)"
ENVIRON="$(read_config environ)"

_bad=''
for _key in AUTH0_DOMAIN AUTH0_CID ENVIRON; do
    eval "_value=\$$_key"
    [ -n "$_value" ] || _bad="$_bad $_key"
done
if [ -n "$_bad" ]; then
    fail "$CONFIG has no usable value for:$_bad"
    fail "a device installed without these cannot authenticate anyone - fix the config and re-run"
    exit 21
fi

for _key in AUTH0_ALGORITHMS CLOUD_DOMAIN GCP_FUNCTIONS_DOMAIN; do
    eval "_value=\$$_key"
    [ -n "$_value" ] || log "WARNING: $_key is not set in $CONFIG - using the container default"
done

MONGO_VERSION='4.2'
REDIS_URL='redis://localhost:6379'
REDIS_SERVER='172.17.0.1'
REDIS_PORT='6379'
DB_NAME='fvonprem'
MONGO_SERVER='172.17.0.1'
MONGO_PORT='27017'
MONGODB_URL='mongodb://localhost:27017'
REMBG_MODEL='u2netp'

# --- host prerequisites -----------------------------------------------------
if [ "$SYSTEM_ARCH" = "arm" ]; then
    # Host node, for node-red tooling. Not fatal: the containers are what serve
    # the line, and aborting a whole install over this would be worse than
    # running degraded and letting the verify phase report it.
    NODE_DIST=node-v10.16.1-linux-arm64
    if wget -q "https://nodejs.org/dist/v10.16.1/${NODE_DIST}.tar.xz" \
       && tar -xJf "${NODE_DIST}.tar.xz" \
       && [ -d "$NODE_DIST" ]; then
        sudo cp -R "${NODE_DIST}/." /usr/local/ \
            || log "WARNING: copying node into /usr/local failed"
        rm -rf "${NODE_DIST}" "${NODE_DIST}.tar.xz"
    else
        log "WARNING: node ${NODE_DIST} download or extract failed - not copying anything"
    fi

    sudo apt-get install -y nvidia-container || \
        log "WARNING: nvidia-container install failed - GPU containers may not start"
fi

if [ "$SYSTEM_ARCH" = "x86" ]; then
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
        gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

    curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
        sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
        tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

    apt-get update
    apt-get install -y nvidia-container-toolkit

    nvidia-ctk runtime configure --runtime=docker
    systemctl restart docker
fi

# --- pull everything before starting anything -------------------------------
IMAGE_MONGO="mongo:$MONGO_VERSION"
IMAGE_CAPDEV="fvonprem/$SYSTEM_ARCH-backend:$CAPDEV_VERSION"
IMAGE_CAPTUREUI="fvonprem/$SYSTEM_ARCH-frontend:$CAPTUREUI_VERSION"
IMAGE_PREDICTION="fvonprem/$SYSTEM_ARCH-prediction:$PREDICTION_VERSION"
IMAGE_PREDICTLITE="fvonprem/$SYSTEM_ARCH-predictlite:$PREDICT_LITE_VERSION"
IMAGE_VISION="fvonprem/$SYSTEM_ARCH-vision:$VISION_VERSION"
IMAGE_NODECREATOR="fvonprem/$SYSTEM_ARCH-nodecreator:$CREATOR_VERSION"
IMAGE_VISIONTOOLS=''
if [ -n "$VISIONTOOLS_ENABLED" ]; then
    IMAGE_VISIONTOOLS="fvonprem/$SYSTEM_ARCH-visiontools:$VISIONTOOLS_VERSION"
fi

_failed_pulls=''
for _image in "$IMAGE_MONGO" "$IMAGE_CAPDEV" "$IMAGE_CAPTUREUI" \
              "$IMAGE_PREDICTION" "$IMAGE_PREDICTLITE" "$IMAGE_VISION" \
              "$IMAGE_NODECREATOR" ${IMAGE_VISIONTOOLS:+"$IMAGE_VISIONTOOLS"}; do
    safe_pull "$_image" || _failed_pulls="$_failed_pulls $_image"
done
if [ -n "$_failed_pulls" ]; then
    fail "could not pull:$_failed_pulls"
    fail "nothing was started - check the versions and the registry, then re-run"
    exit 22
fi

# --- run --------------------------------------------------------------------
# Removing an existing container by the same name makes a re-run after a partial
# install work instead of failing on every name in turn.
start() {
    _name="$1"
    shift
    if docker ps -a --format '{{.Names}}' | grep -q "^${_name}$"; then
        log "$_name already exists - replacing it"
        docker rm -f "$_name" >/dev/null 2>&1 || true
    fi
    if ! docker run "$@"; then
        fail "docker run failed for $_name"
        exit 23
    fi
}

start mongo -p "$MONGO_PORT:$MONGO_PORT" --restart unless-stopped \
    --name mongo -d "$IMAGE_MONGO"

start capdev -d --name=capdev -p 0.0.0.0:5000:5000 --restart unless-stopped \
    --privileged -v /dev:/dev -v /sys:/sys \
    --network host -e ACCESS_KEY=imagerie -e SECRET_KEY=imagerie \
    -v /etc/timezone:/etc/timezone:ro -v /etc/localtime:/etc/localtime:ro \
    -e AUTH0_DOMAIN="$AUTH0_DOMAIN" -e AUTH0_CLIENT_ID="$AUTH0_CID" \
    -e REDIS_URL="$REDIS_URL" -e REDIS_SERVER="$REDIS_SERVER" -e REDIS_PORT="$REDIS_PORT" \
    -e DB_NAME="$DB_NAME" -e MONGO_SERVER="$MONGO_SERVER" -e MONGO_PORT="$MONGO_PORT" \
    -e GCP_FUNCTIONS_DOMAIN="$GCP_FUNCTIONS_DOMAIN" -e CLOUD_DOMAIN="$CLOUD_DOMAIN" \
    -e ENVIRON="$ENVIRON" -e AUTH0_ALGORITHMS="$AUTH0_ALGORITHMS" -e JWT_SECRET="$JWT_SECRET" \
    --log-opt max-size=50m --log-opt max-file=5 \
    "$IMAGE_CAPDEV"

if [ "$ENVIRON" = "local" ]; then
    CAPTUREUI_PORTS='0.0.0.0:3000:3000'
else
    CAPTUREUI_PORTS='0.0.0.0:80:3000'
fi
start captureui -p "$CAPTUREUI_PORTS" --restart unless-stopped \
    --name captureui -e CAPTURE_SERVER=http://172.17.0.1:5000 \
    -e PROCESS_SERVER=http://172.17.0.1 -d --network imagerie_nw \
    --log-opt max-size=50m --log-opt max-file=5 -e REACT_APP_ARCH="$SYSTEM_ARCH" \
    "$IMAGE_CAPTUREUI"

start localprediction -p 8500:8500 -p 8501:8501 --gpus device=0 \
    --name localprediction -d \
    -e AWS_ACCESS_KEY_ID=imagerie -e AWS_SECRET_ACCESS_KEY=imagerie \
    -e AWS_REGION=us-east-1 \
    --restart unless-stopped --network imagerie_nw \
    --log-opt max-size=50m --log-opt max-file=5 \
    -t "$IMAGE_PREDICTION"

start predictlite -p 8511:8511 --name predictlite -d \
    --restart unless-stopped --network imagerie_nw \
    --runtime nvidia \
    --log-opt max-size=50m --log-opt max-file=5 \
    -t "$IMAGE_PREDICTLITE"

start vision -p 5555:5555 --name vision -d \
    --restart unless-stopped --network host \
    --privileged -v /dev:/dev -v /sys:/sys \
    --log-opt max-size=50m --log-opt max-file=5 \
    -e AUTH0_DOMAIN="$AUTH0_DOMAIN" -e AUTH0_CID="$AUTH0_CID" \
    -e REDIS_URL="$REDIS_URL" -e REDIS_SERVER="$REDIS_SERVER" -e REDIS_PORT="$REDIS_PORT" \
    -e DB_NAME="$DB_NAME" -e MONGO_SERVER="$MONGO_SERVER" -e MONGO_PORT="$MONGO_PORT" \
    -t "$IMAGE_VISION"

start nodecreator -d --name=nodecreator -p 0.0.0.0:1880:1880 \
    --restart unless-stopped --privileged -v /dev:/dev -v /sys:/sys \
    --log-opt max-size=50m --log-opt max-file=5 \
    -v /home/visioncell/Documents:/Documents \
    --network host -t "$IMAGE_NODECREATOR"

if [ -n "$VISIONTOOLS_ENABLED" ]; then
start visiontools -d --name=visiontools -p 0.0.0.0:5021:5021 \
    --restart unless-stopped \
    --network imagerie_nw --gpus device=0 -e MONGODB_URL="$MONGODB_URL" \
    -e DB_NAME="$DB_NAME" -e MONGO_SERVER="$MONGO_SERVER" -e MONGO_PORT="$MONGO_PORT" \
    -e REMBG_MODEL="$REMBG_MODEL" -e PYTHONUNBUFFERED=1 \
    -t "$IMAGE_VISIONTOOLS"
fi

# --- MQTT broker ------------------------------------------------------------
if ! "$(dirname "$0")/mqtt/setup_mqtt.sh" "$SYSTEM_ARCH" "$ENVIRON"; then
    fail "MQTT broker setup failed"
    exit 23
fi

# --- verify -----------------------------------------------------------------
# All of them, reporting every failure rather than stopping at the first: an
# engineer on a first install wants the whole picture, not one name at a time.
# capdev is the only one with a readiness endpoint here; the rest are checked
# for having settled, which is weaker and labelled as such.
log 'verifying containers'
_broken=''

smoke_http capdev "http://172.17.0.1:5000/api/capture/auth/jwks" 30 2 \
    || _broken="$_broken capdev"

VERIFY_LIST='mongo captureui localprediction predictlite vision nodecreator'
if [ -n "$VISIONTOOLS_ENABLED" ]; then
    VERIFY_LIST="$VERIFY_LIST visiontools"
fi
for _name in $VERIFY_LIST; do
    smoke_settled "$_name" 8 || _broken="$_broken $_name"
done

if [ -n "$_broken" ]; then
    fail "install finished but these containers are not healthy:$_broken"
    fail "check 'docker logs <name>' - the device is NOT ready for production"
    exit 24
fi

log 'all containers verified'
