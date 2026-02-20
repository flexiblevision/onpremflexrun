CAP_UPTD=$1
CAPUI_UPTD=$2
PREDICT_UPTD=$3
SYSTEM_ARCH=$4
PREDLITE_UPTD=$5
VISION_UPTD=$6
CREATOR_UPTD=$7
VISIONTOOLS_UPTD=$8

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

uuid="$(uuidgen)"
r_path=$HOME"/flex-run/upgrades/upgrade_recorder.py"
num_steps=-1
cur_step=0
for var in "$@"
do
    if [ $var != 'True' ]; then
        num_steps=$((num_steps+1))
    fi
done

# --- Helper Functions ---

safe_pull() {
    local image="$1"
    echo "Pulling image: $image"
    if docker pull "$image"; then
        echo "Pull succeeded: $image"
        return 0
    else
        echo "ERROR: Pull failed for $image — skipping upgrade for this container"
        return 1
    fi
}

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

# --- Begin Upgrades ---

python3 $r_path -i $uuid -s $num_steps

if [ $CAP_UPTD != 'True' ]; then
    python3 $r_path -i $uuid -t 'updating backend server' -c $cur_step

    if safe_pull fvonprem/$4-backend:$CAP_UPTD; then
        #copy camera data to local device
        {
            docker cp capdev:/fvbackend/cameras.json /
        } || {
            echo 'camera config file does not exist'
        }

        remove_container capdev

        docker run -d --name=capdev -p 0.0.0.0:5000:5000 --restart unless-stopped --privileged -v /dev:/dev -v /sys:/sys \
            --network host -e ACCESS_KEY=imagerie -e SECRET_KEY=imagerie \
            -v /etc/timezone:/etc/timezone:ro -v /etc/localtime:/etc/localtime:ro \
            -e AUTH0_DOMAIN=$AUTH0_DOMAIN -e AUTH0_CLIENT_ID=$AUTH0_CID -e TZ=$TZ \
            -e REDIS_URL=$REDIS_URL -e REDIS_SERVER=$REDIS_SERVER -e REDIS_PORT=$REDIS_PORT \
            -e DB_NAME=$DB_NAME -e MONGO_SERVER=$MONGO_SERVER -e MONGO_PORT=$MONGO_PORT \
            -e GCP_FUNCTIONS_DOMAIN=$GCP_FUNCTIONS_DOMAIN -e CLOUD_DOMAIN=$CLOUD_DOMAIN \
            -e ENVIRON=$ENVIRON -e AUTH0_ALGORITHMS=$AUTH0_ALGORITHMS -e JWT_SECRET=$JWT_SECRET \
            --log-opt max-size=50m --log-opt max-file=5 \
            -d fvonprem/$4-backend:$CAP_UPTD

        verify_running capdev

        #upload camera data back to new container
        {
            docker cp /cameras.json capdev:/fvbackend/
        } || {
            echo 'camera config file not found'
        }
    fi

    cur_step=$((cur_step+1))
    python3 $r_path -i $uuid -t 'backend server updated' -c $cur_step
fi

if [ $PREDICT_UPTD != 'True' ]; then
    python3 $r_path -i $uuid -t 'updating inference server' -c $cur_step

    if safe_pull fvonprem/$4-prediction:$PREDICT_UPTD; then
        remove_container localprediction

        docker run -p 8500:8500 -p 8501:8501 --gpus device=0 --name localprediction  -d -e AWS_ACCESS_KEY_ID=imagerie -e AWS_SECRET_ACCESS_KEY=imagerie -e AWS_REGION=us-east-1 \
            --restart unless-stopped --network imagerie_nw  \
            --log-opt max-size=50m --log-opt max-file=5 \
            -t fvonprem/$4-prediction:$PREDICT_UPTD

        verify_running localprediction

        DIR=$HOME"/../models"
        if [ -d "$DIR" ]; then
            docker cp $DIR localprediction:/
            docker restart localprediction
        fi
    fi

    cur_step=$((cur_step+1))
    python3 $r_path -i $uuid -t 'inference server updated' -c $cur_step
fi

if [ $PREDLITE_UPTD != 'True' ]; then
    python3 $r_path -i $uuid -t 'updating inference lite server' -c $cur_step

    if safe_pull fvonprem/$4-predictlite:$PREDLITE_UPTD; then
        remove_container predictlite

        docker run -p 8511:8511 --name predictlite  -d  \
            --restart unless-stopped --network imagerie_nw  \
            --runtime nvidia \
            --log-opt max-size=50m --log-opt max-file=5 \
            -t fvonprem/$4-predictlite:$PREDLITE_UPTD

        verify_running predictlite

        DIR=$HOME"/../lite_models"
        if [ -d "$DIR" ]; then
            docker cp $DIR predictlite:/data/models
        fi
    fi

    cur_step=$((cur_step+1))
    python3 $r_path -i $uuid -t 'updated inference lite server' -c $cur_step
fi

if [ $VISION_UPTD != 'True' ]; then
    python3 $r_path -i $uuid -t 'updating vision server' -c $cur_step

    if safe_pull fvonprem/$4-vision:$VISION_UPTD; then
        {
            docker cp vision:/fvbackend/camera_configs /
        } || {
            echo 'vision config file does not exist'
        }

        remove_container vision

        docker run -p 5555:5555 --name vision  -d  \
            --restart unless-stopped --network host  \
            --privileged -v /dev:/dev -v /sys:/sys \
            --log-opt max-size=50m --log-opt max-file=5 \
            -e AUTH0_DOMAIN=$AUTH0_DOMAIN -e AUTH0_CID=$AUTH0_CID \
            -e REDIS_URL=$REDIS_URL -e REDIS_SERVER=$REDIS_SERVER -e REDIS_PORT=$REDIS_PORT \
            -e DB_NAME=$DB_NAME -e MONGO_SERVER=$MONGO_SERVER -e MONGO_PORT=$MONGO_PORT \
            -t fvonprem/$4-vision:$VISION_UPTD

        verify_running vision

        # upload camera config back into container
        {
            docker cp /camera_configs vision:/fvbackend/
        } || {
            echo 'vision config file not found'
        }
    fi

    cur_step=$((cur_step+1))
    python3 $r_path -i $uuid -t 'updated vision server' -c $cur_step
fi

if [ $CREATOR_UPTD  != 'True' ]; then
    python3 $r_path -i $uuid -t 'updating nodecreator server' -c $cur_step

    if safe_pull fvonprem/$4-nodecreator:$CREATOR_UPTD; then
        {
            docker cp nodecreator:/root/.node-red/flows.json /
        } || {
            echo 'flows file does not exist'
        }

        remove_container nodecreator

        docker run -d --name=nodecreator -p 0.0.0.0:1880:1880 \
        --restart unless-stopped --privileged -v /dev:/dev -v /sys:/sys \
        --log-opt max-size=50m --log-opt max-file=5 \
        -v /home/visioncell/Documents:/Documents \
        --network host -d fvonprem/$4-nodecreator:$CREATOR_UPTD

        verify_running nodecreator

        {
            docker cp /flows.json nodecreator:/root/.node-red/
        } || {
            echo 'flows file not found'
        }
    fi

    cur_step=$((cur_step+1))
    python3 $r_path -i $uuid -t 'updated nodecreator server' -c $cur_step
fi

if [ $VISIONTOOLS_UPTD != 'True' ]; then
    python3 $r_path -i $uuid -t 'updating visiontools server' -c $cur_step

    if safe_pull fvonprem/$4-visiontools:$VISIONTOOLS_UPTD; then
        remove_container visiontools

        docker run -d --name=visiontools -p 0.0.0.0:5021:5021 --restart unless-stopped \
            --network imagerie_nw --gpus device=0 -e MONGODB_URL=$MONGODB_URL \
            -e DB_NAME=$DB_NAME -e MONGO_SERVER=$MONGO_SERVER -e MONGO_PORT=$MONGO_PORT \
            -e REMBG_MODEL=$REMBG_MODEL -e PYTHONUNBUFFERED=1 \
            -d fvonprem/$4-visiontools:$VISIONTOOLS_UPTD

        verify_running visiontools
    fi

    cur_step=$((cur_step+1))
    python3 $r_path -i $uuid -t 'updated visiontools server' -c $cur_step
fi

if [ $CAPUI_UPTD != 'True' ]; then
    python3 $r_path -i $uuid -t 'updating frontend server' -c $cur_step

    if safe_pull fvonprem/$4-frontend:$CAPUI_UPTD; then
        remove_container captureui

        if [ "$ENVIRON" = "local" ]; then
            docker run -p 0.0.0.0:3000:3000 --restart unless-stopped \
                --name captureui -e CAPTURE_SERVER=http://172.17.0.1:5000 -e PROCESS_SERVER=http://172.17.0.1 --network imagerie_nw \
                --log-opt max-size=50m --log-opt max-file=5 -e REACT_APP_ARCH=$4 \
                -d fvonprem/$4-frontend:$CAPUI_UPTD
        else
            docker run -p 0.0.0.0:80:3000 --restart unless-stopped \
                --name captureui -e CAPTURE_SERVER=http://172.17.0.1:5000 -e PROCESS_SERVER=http://172.17.0.1 --network imagerie_nw \
                --log-opt max-size=50m --log-opt max-file=5 -e REACT_APP_ARCH=$4 \
                -d fvonprem/$4-frontend:$CAPUI_UPTD
        fi

        verify_running captureui
    fi

    cur_step=$((cur_step+1))
    python3 $r_path -i $uuid -t 'updated frontend server' -c $cur_step
fi



# VerneMQ MQTT broker - always pull latest for environ
python3 $r_path -i $uuid -t 'updating vernemq broker' -c $cur_step

if safe_pull fvonprem/$4-vernemq:$ENVIRON; then
    remove_container vernemq

    SCRIPT_DIR="$HOME/flex-run/setup/mqtt"
    if ! "$SCRIPT_DIR/setup_mqtt.sh" "$4" "$ENVIRON"; then
        echo "ERROR: setup_mqtt.sh failed — attempting fallback with local image"
        docker run -d \
            --name vernemq \
            --restart unless-stopped \
            --network host \
            --log-opt max-size=50m \
            --log-opt max-file=5 \
            "fvonprem/$4-vernemq:$ENVIRON"
    fi

    verify_running vernemq
else
    echo "Skipping vernemq teardown — pull failed, keeping existing container"
fi

cur_step=$((cur_step+1))
python3 $r_path -i $uuid -t 'updated vernemq broker' -c $cur_step

sh $HOME/flex-run/upgrades/start_servers.sh
