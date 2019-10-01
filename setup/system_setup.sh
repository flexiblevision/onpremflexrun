CAPDEV_VERSION=$1
CAPTUREUI_VERSION=$2
PREDICTION_VERSION=$3
SYSTEM_ARCH=$4

docker run -d --name=capdev -p 0.0.0.0:5000:5000 --restart unless-stopped --privileged -v /dev:/dev -v /sys:/sys \
    --network imagerie_nw -e ACCESS_KEY=imagerie -e SECRET_KEY=imagerie \
    -d fvonprem/$4-backend:$CAPDEV_VERSION

docker run -p 0.0.0.0:80:3000 --restart unless-stopped \
    --name captureui -e CAPTURE_SERVER=http://capdev:5000 -d --network imagerie_nw \
     fvonprem/$4-frontend:$CAPTUREUI_VERSION

docker run -p 8500:8500 -p 8501:8501 --runtime=nvidia --name localprediction  -d -e AWS_ACCESS_KEY_ID=imagerie -e AWS_SECRET_ACCESS_KEY=imagerie -e AWS_REGION=us-east-1 \
    --restart unless-stopped --network imagerie_nw  \
    -t fvonprem/$4-prediction:$PREDICTION_VERSION
