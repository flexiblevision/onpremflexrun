CAP_UPTD=$1
CAPUI_UPTD=$2
PREDICT_UPTD=$3
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
CLOUD_DOMAIN="$(cat ~/flex-run/setup_constants/cloud_domain.txt)"
GCP_FUNCTIONS_DOMAIN="$(cat ~/flex-run/setup_constants/gcp_functions_domain.txt)"

if [ $CAP_UPTD != 'True' ]; then
    docker pull fvonprem/$4-backend:$CAP_UPTD
    #copy camera data to local device
    docker cp capdev:/fvbackend/cameras.json /

    # update capdev
    docker stop capdev
    docker rm capdev
    docker run -d --name=capdev -p 0.0.0.0:5000:5000 --restart unless-stopped --privileged -v /dev:/dev -v /sys:/sys \
        --network imagerie_nw -e ACCESS_KEY=imagerie -e SECRET_KEY=imagerie \
	-e AUTH0_DOMAIN=$AUTH0_DOMAIN -e AUTH0_CLIENT_ID=$AUTH0_CID \
    	-e REDIS_URL=$REDIS_URL -e REDIS_SERVER=$REDIS_SERVER -e REDIS_PORT=$REDIS_PORT \
    	-e DB_NAME=$DB_NAME -e MONGO_SERVER=$MONGO_SERVER -e MONGO_PORT=$MONGO_PORT \
    	-e GCP_FUNCTIONS_DOMAIN=$GCP_FUNCTIONS_DOMAIN -e CLOUD_DOMAIN=$CLOUD_DOMAIN \
        -d fvonprem/$4-backend:$CAP_UPTD

    #upload camera data back to new container
    docker cp /cameras.json capdev:/fvbackend/
fi

if [ $CAPUI_UPTD != 'True' ]; then
    docker pull fvonprem/$4-frontend:$CAPUI_UPTD
    # update captureui
    docker stop captureui
    docker rm captureui
    docker run -p 0.0.0.0:80:3000 --restart unless-stopped \
        --name captureui -e CAPTURE_SERVER=http://172.17.0.1:5000 -e PROCESS_SERVER=http://172.17.0.1 -d --network imagerie_nw \
        fvonprem/$4-frontend:$CAPUI_UPTD
fi

if [ $PREDICT_UPTD != 'True' ]; then
    docker pull fvonprem/$4-prediction:$PREDICT_UPTD
    #update localprediction
    docker stop localprediction
    docker rm localprediction
    docker run -p 8500:8500 -p 8501:8501 --runtime=nvidia --name localprediction  -d -e AWS_ACCESS_KEY_ID=imagerie -e AWS_SECRET_ACCESS_KEY=imagerie -e AWS_REGION=us-east-1 \
        --restart unless-stopped --network imagerie_nw  \
        -t fvonprem/$4-prediction:$PREDICT_UPTD

    DIR=$HOME"/../models"
    if [ -d "$DIR" ]; then
    	docker cp $DIR localprediction:/
	docker restart localprediction
    fi

fi

sh $HOME/flex-run/upgrades/upgrade_flex_run.sh
