CAPDEV_VERSION=$1
CAPTUREUI_VERSION=$2
PREDICTION_VERSION=$3
SYSTEM_ARCH=$4
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
CLOUD_DOMAIN="$(cat ../cloud_domain.txt)"
GCP_FUNCTIONS_DOMAIN='https://us-central1-flexible-vision-staging.cloudfunctions.net/'

docker run -p $MONGO_PORT:$MONGO_PORT --restart unless-stopped  --name mongo -d mongo:$MONGO_VERSION

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
    --network imagerie_nw -e ACCESS_KEY=imagerie -e SECRET_KEY=imagerie \
    -e AUTH0_DOMAIN=$AUTH0_DOMAIN -e AUTH0_CLIENT_ID=$AUTH0_CID \
    -e REDIS_URL=$REDIS_URL -e REDIS_SERVER=$REDIS_SERVER -e REDIS_PORT=$REDIS_PORT \
    -e DB_NAME=$DB_NAME -e MONGO_SERVER=$MONGO_SERVER -e MONGO_PORT=$MONGO_PORT \
    -e GCP_FUNCTIONS_DOMAIN=$GCP_FUNCTIONS_DOMAIN -e CLOUD_DOMAIN=$CLOUD_DOMAIN \
    -d fvonprem/$4-backend:$CAPDEV_VERSION

docker run -p 0.0.0.0:80:3000 --restart unless-stopped \
    --name captureui -e CAPTURE_SERVER=http://capdev:5000 -e PROCESS_SERVER=http://capdev -d --network imagerie_nw \
     fvonprem/$4-frontend:$CAPTUREUI_VERSION

docker run -p 8500:8500 -p 8501:8501 --runtime=nvidia --name localprediction  -d -e AWS_ACCESS_KEY_ID=imagerie -e AWS_SECRET_ACCESS_KEY=imagerie -e AWS_REGION=us-east-1 \
    --restart unless-stopped --network imagerie_nw  \
    -t fvonprem/$4-prediction:$PREDICTION_VERSION
