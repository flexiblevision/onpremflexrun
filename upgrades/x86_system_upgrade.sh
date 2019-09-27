#copy user data to local device
docker cp capdev:/fvonprem/db.json /
docker cp capdev:/fvonprem/cameras.json /

# update capdev
docker stop capdev
docker rm capdev
docker run -d --name=capdev -p 0.0.0.0:5000:5000 --restart unless-stopped --privileged -v /dev:/dev -v /sys:/sys \
    --network imagerie_nw -e ACCESS_KEY=imagerie -e SECRET_KEY=imagerie \
    -d fvonprem/x86-backend:latest 

#upload copied user data back to new containers
docker cp /db.json capdev:/fvonprem/
docker cp /cameras.json capdev:/fvonprem/

# update captureui
docker stop captureui
docker rm captureui
docker run -p 0.0.0.0:80:3000 --restart unless-stopped \
    --name captureui -e CAPTURE_SERVER=http://capdev:5000 -e PROCESS_SERVER=http://capdev -d --network imagerie_nw \
     fvonprem/x86-frontend:latest 

#update localprediction
#docker stop localprediction
#docker rm localprediction
#docker run -p 8500:8500 -p 8501:8501 --runtime=nvidia --name localprediction  -d -e AWS_ACCESS_KEY_ID=imagerie -e AWS_SECRET_ACCESS_KEY=imagerie -e AWS_REGION=us-east-1 \
    #--restart unless-stopped --network imagerie_nw  \
    #-t fvonprem/x86-prediction:latest 
