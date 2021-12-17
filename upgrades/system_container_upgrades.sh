CAP_UPTD=$1
CAPUI_UPTD=$2
PREDICT_UPTD=$3
SYSTEM_ARCH=$4
PREDLITE_UPTD=$5
VISION_UPTD=$6
CREATOR_UPTD=$7

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
TZ="$(cat /etc/timezone)"

if [ $CAP_UPTD != 'True' ]; then
    docker pull fvonprem/$4-backend:$CAP_UPTD
    #copy camera data to local device
    {
        docker cp capdev:/fvbackend/cameras.json /
    } || {
        echo 'camera config file does not exist'
    }

    # update capdev
    {
        docker stop capdev
        docker rm capdev
    } || {
        echo 'capdev does not exist to remove'
    }
    docker run -d --name=capdev -p 0.0.0.0:5000:5000 --restart unless-stopped --privileged -v /dev:/dev -v /sys:/sys \
        --network host -e ACCESS_KEY=imagerie -e SECRET_KEY=imagerie \
        -v /etc/timezone:/etc/timezone:ro -v /etc/localtime:/etc/localtime:ro \
        -e AUTH0_DOMAIN=$AUTH0_DOMAIN -e AUTH0_CLIENT_ID=$AUTH0_CID -e TZ=$TZ \
        -e REDIS_URL=$REDIS_URL -e REDIS_SERVER=$REDIS_SERVER -e REDIS_PORT=$REDIS_PORT \
        -e DB_NAME=$DB_NAME -e MONGO_SERVER=$MONGO_SERVER -e MONGO_PORT=$MONGO_PORT \
        -e GCP_FUNCTIONS_DOMAIN=$GCP_FUNCTIONS_DOMAIN -e CLOUD_DOMAIN=$CLOUD_DOMAIN \
        -d fvonprem/$4-backend:$CAP_UPTD

    #upload camera data back to new container
    {
        docker cp /cameras.json capdev:/fvbackend/
    } || {
        echo 'camera config file not found'
    }
fi

if [ $PREDICT_UPTD != 'True' ]; then
    docker pull fvonprem/$4-prediction:$PREDICT_UPTD
    #update localprediction
    {
        docker stop localprediction
        docker rm localprediction
    } || {
        echo 'localprediction does not exist to remove'
    }
    docker run -p 8500:8500 -p 8501:8501 --runtime=nvidia --name localprediction  -d -e AWS_ACCESS_KEY_ID=imagerie -e AWS_SECRET_ACCESS_KEY=imagerie -e AWS_REGION=us-east-1 \
        --restart unless-stopped --network imagerie_nw  \
        -t fvonprem/$4-prediction:$PREDICT_UPTD

    DIR=$HOME"/../models"
    if [ -d "$DIR" ]; then
    	docker cp $DIR localprediction:/
	docker restart localprediction
    fi

fi

if [ $PREDLITE_UPTD != 'True' ]; then
    docker pull fvonprem/$4-predictlite:$PREDLITE_UPTD 
    #update predictlite
    {
        docker stop predictlite
        docker rm predictlite

    } || {
        echo 'predictlite does not exist to remove'
    }
    
    docker run -p 8511:8511 --name predictlite  -d  \
        --restart unless-stopped --network imagerie_nw  \
        -t fvonprem/$4-predictlite:$PREDLITE_UPTD

    DIR=$HOME"/../lite_models"
    if [ -d "$DIR" ]; then
        docker cp $DIR predictlite:/data/models
    fi

fi

if [ $VISION_UPTD != 'True' ]; then
    docker pull fvonprem/$4-predictlite:$VISION_UPTD 
    #update vision
    {
        docker cp vision:/fvbackend/vision.json /
    } || {
        echo 'vision config file does not exist'
    }


    {
        docker stop vision
        docker rm vision

    } || {
        echo 'vision does not exist to remove'
    }
    
    docker run -p 5555:5555 --name vision  -d  \
        --restart unless-stopped --network host  \
        --privileged -v /dev:/dev -v /sys:/sys \
        -t fvonprem/$4-vision:$VISION_UPTD

    # upload camera config back into container
    {
        docker cp /vision.json vision:/fvbackend/
    } || {
        echo 'vision config file not found'
    }

fi

if [ $CREATOR_UPTD  != 'True' ]; then
    docker pull fvonprem/$4-nodecreator:$CREATOR_UPTD  

    {
        docker cp nodecreator:/root/.node-red/flows.json /
    } || {
        echo 'flows file does not exist'
    }

    {
        docker stop nodecreator
        docker rm nodecreator

    } || {
        echo 'Node Creator does not exist to remove'
    }
    
    docker run -d --name=nodecreator -p 0.0.0.0:1880:1880 \
    --restart unless-stopped --privileged -v /dev:/dev -v /sys:/sys \
    --network host -d fvonprem/$4-nodecreator:$CREATOR_UPTD 

    {
        docker cp /flows.json nodecreator:/root/.node-red/
    } || {
        echo 'flows file not found'
    }

fi

if [ $CAPUI_UPTD != 'True' ]; then
    docker pull fvonprem/$4-frontend:$CAPUI_UPTD
    # update captureui
    {
        docker stop captureui
        docker rm captureui
    } || {
        echo 'captureui does not exist to remove'
    }
    docker run -p 0.0.0.0:80:3000 --restart unless-stopped \
        --name captureui -e CAPTURE_SERVER=http://172.17.0.1:5000 -e PROCESS_SERVER=http://172.17.0.1 -d --network imagerie_nw \
        fvonprem/$4-frontend:$CAPUI_UPTD
fi



sh $HOME/flex-run/upgrades/start_servers.sh
