CAPDEV_VERSION=$1
CAPTUREUI_VERSION=$2
PREDICTION_VERSION=$3
SYSTEM_ARCH=$4
PREDICT_LITE_VERSION=$5
VISION_VERSION=$6
CREATOR_VERSION=$7
VISIONTOOLS_VERSION=$8

REDIS_VERSION='5.0.6'
MONGO_VERSION='4.2'

AUTH0_DOMAIN='auth.flexiblevision.com'
AUTH0_CID='512rYG6XL32k3uiFg38HQ8fyubOOUUKf'
REDIS_URL='redis://localhost:6379'
REDIS_SERVER='172.17.0.1'
REDIS_PORT='6379'
DB_NAME='fvonprem'
MONGO_SERVER='172.17.0.1'
MONGO_PORT='27017'
MONGODB_URL='mongodb://localhost:27017'
REMBG_MODEL='u2netp'
CLOUD_DOMAIN="$(cat ~/flex-run/setup_constants/cloud_domain.txt)"
GCP_FUNCTIONS_DOMAIN="$(cat ~/flex-run/setup_constants/gcp_functions_domain.txt)"

docker run -p $MONGO_PORT:$MONGO_PORT --restart unless-stopped  --name mongo -d mongo:$MONGO_VERSION

if [ "$SYSTEM_ARCH" = "arm" ]; then
    wget https://nodejs.org/dist/v10.16.1/node-v10.16.1-linux-arm64.tar.xz
    tar -xJf node-v10.16.1-linux-armv6l.tar.xz
    cd node-v10.16.1-linux-armv6l/
    sudo cp -R * /usr/local/

    sudo apt-get install nvidia-container
fi 

if [ "$SYSTEM_ARCH" = "x86" ]; then
    docker volume ls -q -f driver=nvidia-docker | xargs -r -I{} -n1 docker ps -q -a -f volume={} | xargs -r docker rm -f
    curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | \
        apt-key add -
    distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
    curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.list | \
        tee /etc/apt/sources.list.d/nvidia-docker.list
    apt update
    apt-get install -y nvidia-docker2
    pkill -SIGHUP dockerd
    docker run --runtime=nvidia --rm nvidia/cuda:9.0-base nvidia-smi
fi

docker run -d --name=capdev -p 0.0.0.0:5000:5000 --restart unless-stopped --privileged -v /dev:/dev -v /sys:/sys \
    --network host -e ACCESS_KEY=imagerie -e SECRET_KEY=imagerie \
    -v /etc/timezone:/etc/timezone:ro -v /etc/localtime:/etc/localtime:ro \
    -e AUTH0_DOMAIN=$AUTH0_DOMAIN -e AUTH0_CLIENT_ID=$AUTH0_CID \
    -e REDIS_URL=$REDIS_URL -e REDIS_SERVER=$REDIS_SERVER -e REDIS_PORT=$REDIS_PORT \
    -e DB_NAME=$DB_NAME -e MONGO_SERVER=$MONGO_SERVER -e MONGO_PORT=$MONGO_PORT \
    -e GCP_FUNCTIONS_DOMAIN=$GCP_FUNCTIONS_DOMAIN -e CLOUD_DOMAIN=$CLOUD_DOMAIN \
    --log-opt max-size=50m --log-opt max-file=5 \
    -d fvonprem/$4-backend:$CAPDEV_VERSION

docker run -p 0.0.0.0:80:3000 --restart unless-stopped \
    --name captureui -e CAPTURE_SERVER=http://172.17.0.1:5000 -e PROCESS_SERVER=http://172.17.0.1 -d --network imagerie_nw \
    --log-opt max-size=50m --log-opt max-file=5 -e REACT_APP_ARCH=$4 \
     fvonprem/$4-frontend:$CAPTUREUI_VERSION

docker run -p 8500:8500 -p 8501:8501 --runtime=nvidia --name localprediction  -d -e AWS_ACCESS_KEY_ID=imagerie -e AWS_SECRET_ACCESS_KEY=imagerie -e AWS_REGION=us-east-1 \
    --restart unless-stopped --network imagerie_nw  \
    --log-opt max-size=50m --log-opt max-file=5 \
    -t fvonprem/$4-prediction:$PREDICTION_VERSION

docker run -p 8511:8511 --name predictlite  -d  \
    --restart unless-stopped --network imagerie_nw  \
    --log-opt max-size=50m --log-opt max-file=5 \
    -t fvonprem/$4-predictlite:$PREDICT_LITE_VERSION

docker run -p 5555:5555 --name vision  -d  \
    --restart unless-stopped --network host  \
    --privileged -v /dev:/dev -v /sys:/sys \
    --log-opt max-size=50m --log-opt max-file=5 \
    -e AUTH0_DOMAIN=$AUTH0_DOMAIN -e AUTH0_CID=$AUTH0_CID \
    -e REDIS_URL=$REDIS_URL -e REDIS_SERVER=$REDIS_SERVER -e REDIS_PORT=$REDIS_PORT \
    -e DB_NAME=$DB_NAME -e MONGO_SERVER=$MONGO_SERVER -e MONGO_PORT=$MONGO_PORT \
    -t fvonprem/$4-vision:$VISION_VERSION

docker run -d --name=nodecreator -p 0.0.0.0:1880:1880 \
    --restart unless-stopped --privileged -v /dev:/dev -v /sys:/sys \
    --log-opt max-size=50m --log-opt max-file=5 \
    -v /home/visioncell/Documents:/Documents \
    --network host -t fvonprem/$4-nodecreator:$CREATOR_VERSION 

docker run -d --name=visiontools -p 0.0.0.0:5021:5021 --restart unless-stopped \
    --network imagerie_nw --runtime=nvidia -e MONGODB_URL=$MONGODB_URL \
    -e DB_NAME=$DB_NAME -e MONGO_SERVER=$MONGO_SERVER -e MONGO_PORT=$MONGO_PORT \
    -e REMBG_MODEL=$REMBG_MODEL -e PYTHONUNBUFFERED=1 \
    -d fvonprem/x86-visiontools:VISIONTOOLS_VERSION
