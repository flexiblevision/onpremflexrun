CAP_UPTD_ARG=$1
CAPUI_UPTD_ARG=$2
PREDICT_UPTD_ARG=$3
SYSTEM_ARCH=$4
PREDLITE_UPTD_ARG=$5
VISION_UPTD_ARG=$6
CREATOR_UPTD_ARG=$7
VISIONTOOLS_UPTD_ARG=$8

REDIS_VERSION='5.0.6'
MONGO_VERSION='4.2'

AUTH0_DOMAIN="$(jq -r '.auth0_domain' ~/fvconfig.json)"
AUTH0_CID='512rYG6XL32k3uiFg38HQ8fyubOOUUKf'
AUTH0_ALGORITHMS="$(jq -r '.auth_alg' ~/fvconfig.json)"
JWT_SECRET="$(jq -r '.jwt_secret_key' ~/fvconfig.json)"
REDIS_URL='redis://localhost:6379'
REDIS_SERVER='172.17.0.1'
REDIS_PORT='6379'
DB_NAME='fvonprem'
MONGO_SERVER='172.17.0.1'
MONGO_PORT='27017'
MONGODB_URL='mongodb://localhost:27017'
REMBG_MODEL='u2netp'
CLOUD_DOMAIN="$(jq -r '.cloud_domain' ~/fvconfig.json)"
GCP_FUNCTIONS_DOMAIN="$(jq -r '.gcp_functions_domain' ~/fvconfig.json)"
ENVIRON="$(jq -r '.environ' ~/fvconfig.json)"
TZ="$(cat /etc/timezone)"

# The detached runner passes its run id so the API and the step records refer
# to the same upgrade. Standalone invocations still get their own id.
uuid="${FLEXRUN_RUN_ID:-$(uuidgen)}"
r_path="$HOME/flex-run/upgrades/upgrade_recorder.py"
FLEXRUN_RECORDER="$r_path"
export FLEXRUN_RECORDER
num_steps=0
cur_step=0

# Container state is staged here while a container is torn down and recreated.
# Never in / — a crash between teardown and recreation orphans the only copy at
# the filesystem root, and two concurrent runs overwrite each other's config.
STAGING_ROOT="${FLEXRUN_STAGING_DIR:-/var/lib/flex-run/staging}"
STAGING="$STAGING_ROOT/$uuid"
mkdir -p "$STAGING"
chmod 700 "$STAGING"

# Staged copies are kept as a recovery artifact; prune old runs so this does not
# grow without bound.
find "$STAGING_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime +30 \
    -exec rm -rf {} + 2>/dev/null

# Shared deploy helpers - retire/rollback/smoke live here so the swap logic is
# in one place rather than repeated per container.
for _lib in "$(dirname "$0")/lib/deploy_common.sh" \
            "$HOME/flex-run/upgrades/lib/deploy_common.sh"; do
    if [ -r "$_lib" ]; then
        . "$_lib"
        _lib_loaded=1
        break
    fi
done
if [ -z "${_lib_loaded:-}" ]; then
    echo "ERROR: cannot find upgrades/lib/deploy_common.sh - deploy tree is incomplete" >&2
    exit 1
fi

# The plan is the source of truth when a signed release drove this run; the
# positional arguments are the fallback for an older runner that has none.
CAP_UPTD="$(plan_version backend "$CAP_UPTD_ARG")"
CAPUI_UPTD="$(plan_version frontend "$CAPUI_UPTD_ARG")"
PREDICT_UPTD="$(plan_version prediction "$PREDICT_UPTD_ARG")"
PREDLITE_UPTD="$(plan_version predictlite "$PREDLITE_UPTD_ARG")"
VISION_UPTD="$(plan_version vision "$VISION_UPTD_ARG")"
CREATOR_UPTD="$(plan_version nodecreator "$CREATOR_UPTD_ARG")"
VISIONTOOLS_UPTD="$(plan_version visiontools "$VISIONTOOLS_UPTD_ARG")"

# vernemq had no positional slot at all - it was upgraded unconditionally with
# $ENVIRON as its tag, which is a channel name and not a version. With a plan it
# is an ordinary component; without one it keeps the old behaviour.
VERNEMQ_UPTD="$(plan_version vernemq "$ENVIRON")"

# Counted after resolution, not from $@: with a plan the versions do not come
# from the argument list at all, and a step count taken from argv would leave
# the progress bar reporting against the wrong total.
for _v in "$CAP_UPTD" "$CAPUI_UPTD" "$PREDICT_UPTD" "$PREDLITE_UPTD" \
          "$VISION_UPTD" "$CREATOR_UPTD" "$VISIONTOOLS_UPTD" "$VERNEMQ_UPTD"; do
    [ "$_v" != 'True' ] && num_steps=$((num_steps+1))
done

# --- Helper Functions ---

verify_running() {
    local container="$1"
    local retries=3
    local wait=3
    local i=0
    while [ $i -lt $retries ]; do
        if docker ps --format '{{.Names}}' | grep -q "^${container}$"; then
            echo "Verified: $container is running"
            return 0
        fi
        i=$((i+1))
        echo "Waiting for $container to start (attempt $i/$retries)..."
        sleep $wait
    done
    echo "ERROR: $container is NOT running after $retries checks"
    return 1
}

remove_container() {
    local container="$1"
    if docker ps -a --format '{{.Names}}' | grep -q "^${container}$"; then
        docker stop "$container" 2>/dev/null
        docker rm "$container" 2>/dev/null
    fi
}

# save_state <container> <path-in-container> <staged-name>
# Returns 0 when it is safe to tear the container down: either the state was
# copied out, or there is genuinely nothing to copy. Returns 1 when the state
# exists but could not be saved — the caller must then leave the container
# alone rather than destroy the only copy.
save_state() {
    local container="$1"
    local src="$2"
    local name="$3"

    if ! docker inspect "$container" >/dev/null 2>&1; then
        echo "$container does not exist yet - no state to preserve"
        return 0
    fi

    if docker cp "$container:$src" "$STAGING/$name" 2>/dev/null; then
        echo "saved $container:$src to $STAGING/$name"
        return 0
    fi

    if docker exec "$container" test -e "$src" >/dev/null 2>&1; then
        echo "ERROR: $container:$src exists but could not be copied to $STAGING"
        echo "ERROR: leaving $container in place - skipping its upgrade"
        return 1
    fi

    echo "$container has no $src - nothing to preserve"
    return 0
}

# restore_state <container> <destination-dir-in-container> <staged-name>
restore_state() {
    local container="$1"
    local dest="$2"
    local name="$3"

    if [ ! -e "$STAGING/$name" ]; then
        echo "nothing staged for $container:$dest"
        return 0
    fi

    if docker cp "$STAGING/$name" "$container:$dest"; then
        echo "restored $name into $container:$dest"
        return 0
    fi

    echo "ERROR: could not restore $name into $container - copy kept at $STAGING/$name"
    return 1
}

# --- Begin Upgrades ---

python3 "$r_path" -i "$uuid" -s "$num_steps"

if [ "$CAP_UPTD" != 'True' ]; then
    python3 "$r_path" -i "$uuid" -t 'updating backend server' -c "$cur_step"

    # The image is pulled BEFORE anything is torn down, so a bad network fails
    # the swap closed instead of half-way through it.
    REF_BACKEND="$(plan_ref backend "fvonprem/$SYSTEM_ARCH-backend:$CAP_UPTD")"
    if safe_pull "$REF_BACKEND" && \
       save_state capdev /fvbackend/cameras.json cameras.json && \
       retire_container capdev; then

        docker run -d --name=capdev -p 0.0.0.0:5000:5000 --restart unless-stopped --privileged -v /dev:/dev -v /sys:/sys \
            --network host -e ACCESS_KEY=imagerie -e SECRET_KEY=imagerie \
            -v /etc/timezone:/etc/timezone:ro -v /etc/localtime:/etc/localtime:ro \
            -e AUTH0_DOMAIN="$AUTH0_DOMAIN" -e AUTH0_CLIENT_ID="$AUTH0_CID" -e TZ="$TZ" \
            -e REDIS_URL="$REDIS_URL" -e REDIS_SERVER="$REDIS_SERVER" -e REDIS_PORT="$REDIS_PORT" \
            -e DB_NAME="$DB_NAME" -e MONGO_SERVER="$MONGO_SERVER" -e MONGO_PORT="$MONGO_PORT" \
            -e GCP_FUNCTIONS_DOMAIN="$GCP_FUNCTIONS_DOMAIN" -e CLOUD_DOMAIN="$CLOUD_DOMAIN" \
            -e ENVIRON="$ENVIRON" -e AUTH0_ALGORITHMS="$AUTH0_ALGORITHMS" -e JWT_SECRET="$JWT_SECRET" \
            --log-opt max-size=50m --log-opt max-file=5 \
            -d "$REF_BACKEND"

        # Readiness, not liveness: the jwks endpoint is only served once the
        # backend is actually up, so this catches a container that started and
        # then could not reach Mongo.
        if smoke_http capdev "http://172.17.0.1:5000/api/capture/auth/jwks"; then
            restore_state capdev /fvbackend/ cameras.json
            report_component backend upgraded "" "$CAP_UPTD" "$REF_BACKEND"
        discard_previous capdev
        else
            report_component backend rolled_back "" "$CAP_UPTD" "$REF_BACKEND"
        rollback_container capdev
            restore_state capdev /fvbackend/ cameras.json
        fi
    fi

    cur_step=$((cur_step+1))
    python3 "$r_path" -i "$uuid" -t 'backend server updated' -c "$cur_step"
fi

if [ "$PREDICT_UPTD" != 'True' ]; then
    python3 "$r_path" -i "$uuid" -t 'updating inference server' -c "$cur_step"

    REF_PREDICTION="$(plan_ref prediction "fvonprem/$SYSTEM_ARCH-prediction:$PREDICT_UPTD")"
    if safe_pull "$REF_PREDICTION" && \
       retire_container localprediction; then

        # Mirrors base_path() in worker_scripts/retrieve_models.py.
        MODELS_DIR="$([ -d /xavier_ssd ] && echo /xavier_ssd/models || echo /models)"
        mkdir -p "$MODELS_DIR"

        docker run -p 8500:8500 -p 8501:8501 --gpus device=0 --name localprediction  -d -e AWS_ACCESS_KEY_ID=imagerie -e AWS_SECRET_ACCESS_KEY=imagerie -e AWS_REGION=us-east-1 \
            --restart unless-stopped --network imagerie_nw  \
            --log-opt max-size=50m --log-opt max-file=5 \
            -v "$MODELS_DIR:/models" \
            -t "$REF_PREDICTION"

        # After the model copy and restart, not before - restarting a container
        # that had already passed would leave a pass standing over a new start.
        if smoke_running localprediction; then
            report_component prediction upgraded "" "$PREDICT_UPTD" "$REF_PREDICTION"
        discard_previous localprediction
        else
            report_component prediction rolled_back "" "$PREDICT_UPTD" "$REF_PREDICTION"
        rollback_container localprediction
        fi
    fi

    cur_step=$((cur_step+1))
    python3 "$r_path" -i "$uuid" -t 'inference server updated' -c "$cur_step"
fi

if [ "$PREDLITE_UPTD" != 'True' ]; then
    python3 "$r_path" -i "$uuid" -t 'updating inference lite server' -c "$cur_step"

    REF_PREDICTLITE="$(plan_ref predictlite "fvonprem/$SYSTEM_ARCH-predictlite:$PREDLITE_UPTD")"
    if safe_pull "$REF_PREDICTLITE" && \
       retire_container predictlite; then

        # Mirrors base_path() in worker_scripts/retrieve_models.py.
        LITE_MODELS_DIR="$([ -d /xavier_ssd ] && echo /xavier_ssd/lite_models || echo /lite_models)"
        mkdir -p "$LITE_MODELS_DIR"

        docker run -p 8511:8511 --name predictlite  -d  \
            --restart unless-stopped --network imagerie_nw  \
            --runtime nvidia \
            --log-opt max-size=50m --log-opt max-file=5 \
            -v "$LITE_MODELS_DIR:/data/lite_models" \
            -t "$REF_PREDICTLITE"

        if smoke_running predictlite; then
            report_component predictlite upgraded "" "$PREDLITE_UPTD" "$REF_PREDICTLITE"
        discard_previous predictlite
        else
            report_component predictlite rolled_back "" "$PREDLITE_UPTD" "$REF_PREDICTLITE"
        rollback_container predictlite
        fi
    fi

    cur_step=$((cur_step+1))
    python3 "$r_path" -i "$uuid" -t 'updated inference lite server' -c "$cur_step"
fi

if [ "$VISION_UPTD" != 'True' ]; then
    python3 "$r_path" -i "$uuid" -t 'updating vision server' -c "$cur_step"

    REF_VISION="$(plan_ref vision "fvonprem/$SYSTEM_ARCH-vision:$VISION_UPTD")"
    if safe_pull "$REF_VISION" && \
       save_state vision /fvbackend/camera_configs camera_configs && \
       retire_container vision; then

        docker run -p 5555:5555 --name vision  -d  \
            --restart unless-stopped --network host  \
            --privileged -v /dev:/dev -v /sys:/sys \
            --log-opt max-size=50m --log-opt max-file=5 \
            -e AUTH0_DOMAIN="$AUTH0_DOMAIN" -e AUTH0_CID="$AUTH0_CID" \
            -e REDIS_URL="$REDIS_URL" -e REDIS_SERVER="$REDIS_SERVER" -e REDIS_PORT="$REDIS_PORT" \
            -e DB_NAME="$DB_NAME" -e MONGO_SERVER="$MONGO_SERVER" -e MONGO_PORT="$MONGO_PORT" \
            -t "$REF_VISION"

        if smoke_running vision; then
            restore_state vision /fvbackend/ camera_configs
            report_component vision upgraded "" "$VISION_UPTD" "$REF_VISION"
        discard_previous vision
        else
            report_component vision rolled_back "" "$VISION_UPTD" "$REF_VISION"
        rollback_container vision
            restore_state vision /fvbackend/ camera_configs
        fi
    fi

    cur_step=$((cur_step+1))
    python3 "$r_path" -i "$uuid" -t 'updated vision server' -c "$cur_step"
fi

if [ "$CREATOR_UPTD"  != 'True' ]; then
    python3 "$r_path" -i "$uuid" -t 'updating nodecreator server' -c "$cur_step"

    REF_NODECREATOR="$(plan_ref nodecreator "fvonprem/$SYSTEM_ARCH-nodecreator:$CREATOR_UPTD")"
    if safe_pull "$REF_NODECREATOR" && \
       save_state nodecreator /root/.node-red/flows.json flows.json && \
       retire_container nodecreator; then

        docker run -d --name=nodecreator -p 0.0.0.0:1880:1880 \
        --restart unless-stopped --privileged -v /dev:/dev -v /sys:/sys \
        --log-opt max-size=50m --log-opt max-file=5 \
        -v /home/visioncell/Documents:/Documents \
        --network host -d "$REF_NODECREATOR"

        if smoke_running nodecreator; then
            restore_state nodecreator /root/.node-red/ flows.json
            report_component nodecreator upgraded "" "$CREATOR_UPTD" "$REF_NODECREATOR"
        discard_previous nodecreator
        else
            report_component nodecreator rolled_back "" "$CREATOR_UPTD" "$REF_NODECREATOR"
        rollback_container nodecreator
            restore_state nodecreator /root/.node-red/ flows.json
        fi
    fi

    cur_step=$((cur_step+1))
    python3 "$r_path" -i "$uuid" -t 'updated nodecreator server' -c "$cur_step"
fi

if [ "$VISIONTOOLS_UPTD" != 'True' ]; then
    python3 "$r_path" -i "$uuid" -t 'updating visiontools server' -c "$cur_step"

    REF_VISIONTOOLS="$(plan_ref visiontools "fvonprem/$SYSTEM_ARCH-visiontools:$VISIONTOOLS_UPTD")"
    if safe_pull "$REF_VISIONTOOLS" && \
       retire_container visiontools; then

        docker run -d --name=visiontools -p 0.0.0.0:5021:5021 --restart unless-stopped \
            --network imagerie_nw --gpus device=0 -e MONGODB_URL="$MONGODB_URL" \
            -e DB_NAME="$DB_NAME" -e MONGO_SERVER="$MONGO_SERVER" -e MONGO_PORT="$MONGO_PORT" \
            -e REMBG_MODEL="$REMBG_MODEL" -e PYTHONUNBUFFERED=1 \
            -d "$REF_VISIONTOOLS"

        if smoke_running visiontools; then
            report_component visiontools upgraded "" "$VISIONTOOLS_UPTD" "$REF_VISIONTOOLS"
        discard_previous visiontools
        else
            report_component visiontools rolled_back "" "$VISIONTOOLS_UPTD" "$REF_VISIONTOOLS"
        rollback_container visiontools
        fi
    fi

    cur_step=$((cur_step+1))
    python3 "$r_path" -i "$uuid" -t 'updated visiontools server' -c "$cur_step"
fi

if [ "$CAPUI_UPTD" != 'True' ]; then
    python3 "$r_path" -i "$uuid" -t 'updating frontend server' -c "$cur_step"

    REF_FRONTEND="$(plan_ref frontend "fvonprem/$SYSTEM_ARCH-frontend:$CAPUI_UPTD")"
    if safe_pull "$REF_FRONTEND" && \
       retire_container captureui; then

        if [ "$ENVIRON" = "local" ]; then
            docker run -p 0.0.0.0:3000:3000 --restart unless-stopped \
                --name captureui -e CAPTURE_SERVER=http://172.17.0.1:5000 -e PROCESS_SERVER=http://172.17.0.1 --network imagerie_nw \
                --log-opt max-size=50m --log-opt max-file=5 -e REACT_APP_ARCH="$SYSTEM_ARCH" \
                -d "$REF_FRONTEND"
        else
            docker run -p 0.0.0.0:80:3000 --restart unless-stopped \
                --name captureui -e CAPTURE_SERVER=http://172.17.0.1:5000 -e PROCESS_SERVER=http://172.17.0.1 --network imagerie_nw \
                --log-opt max-size=50m --log-opt max-file=5 -e REACT_APP_ARCH="$SYSTEM_ARCH" \
                -d "$REF_FRONTEND"
        fi

        if smoke_running captureui; then
            report_component frontend upgraded "" "$CAPUI_UPTD" "$REF_FRONTEND"
        discard_previous captureui
        else
            report_component frontend rolled_back "" "$CAPUI_UPTD" "$REF_FRONTEND"
        rollback_container captureui
        fi
    fi

    cur_step=$((cur_step+1))
    python3 "$r_path" -i "$uuid" -t 'updated frontend server' -c "$cur_step"
fi



# VerneMQ MQTT broker - always pull latest for environ
python3 "$r_path" -i "$uuid" -t 'updating vernemq broker' -c "$cur_step"

REF_VERNEMQ="$(plan_ref vernemq "fvonprem/$SYSTEM_ARCH-vernemq:$VERNEMQ_UPTD")"
if [ "$VERNEMQ_UPTD" = 'True' ]; then
    echo "vernemq already at the released version - skipping"
elif safe_pull "$REF_VERNEMQ" && \
   retire_container vernemq; then

    SCRIPT_DIR="$HOME/flex-run/setup/mqtt"
    if ! "$SCRIPT_DIR/setup_mqtt.sh" "$4" "$ENVIRON"; then
        echo "ERROR: setup_mqtt.sh failed — attempting fallback with local image"
        docker run -d \
            --name vernemq \
            --restart unless-stopped \
            --network host \
            --log-opt max-size=50m \
            --log-opt max-file=5 \
            "$REF_VERNEMQ"
    fi

    if smoke_running vernemq; then
        discard_previous vernemq
    else
        rollback_container vernemq
    fi
else
    echo "Skipping vernemq teardown — pull failed, keeping existing container"
fi

cur_step=$((cur_step+1))
python3 "$r_path" -i "$uuid" -t 'updated vernemq broker' -c "$cur_step"

sh "$HOME/flex-run/upgrades/start_servers.sh"
