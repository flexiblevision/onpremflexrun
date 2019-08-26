# update capdev
docker pull agoeckel/onprem-capdev
docker stop capdev
docker rm capdev
docker run -d --name=capdev -p 0.0.0.0:5000:5000 --restart unless-stopped --privileged -v /dev:/dev -v /sys:/sys \
    --network imagerie_nw -e ACCESS_KEY=imagerie -e SECRET_KEY=imagerie -v $HOME/fv_do_not_delete/fvision_creds.json:/credentials.json \
    -d -e "GOOGLE_APPLICATION_CREDENTIALS=/credentials.json" agoeckel/onprem-capdev

# update captureui
docker pull agoeckel/onprem
docker stop captureui
docker rm captureui
docker run -p 0.0.0.0:80:3000 --restart unless-stopped -v $HOME/fv_do_not_delete/fvision_creds.json:/credentials.json \
    --name captureui -e CAPTURE_SERVER=http://capdev:5000 -d --network imagerie_nw \
     agoeckel/onprem

# update localprediction
docker pull tensorflow/serving:1.12.0-gpu
docker stop localprediction
docker rm localprediction
docker run -p 8500:8500 -p 8501:8501 --runtime=nvidia --name localprediction  -d -e AWS_ACCESS_KEY_ID=imagerie -e AWS_SECRET_ACCESS_KEY=imagerie -e AWS_REGION=us-east-1 \
  --restart unless-stopped --network imagerie_nw \
  -t tensorflow/serving:1.12.0-gpu --model_config_file=/trained_models/model.config
