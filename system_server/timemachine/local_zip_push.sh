
AUTH0_DOMAIN='auth.flexiblevision.com'
AUTH0_CID='512rYG6XL32k3uiFg38HQ8fyubOOUUKf'
REDIS_URL='redis://localhost:6379'
REDIS_SERVER='172.17.0.1'
REDIS_PORT='6379'
DB_NAME='fvonprem'
MONGO_SERVER='172.17.0.1'
MONGO_PORT='27017'
CLOUD_DOMAIN="$(jq '.cloud_domain' ~/fvconfig.json)"

#start eventor server
docker stop eventor
docker rm eventor
docker run -p 1934-1945:1934-1945 --network=host --name eventor -d \
    --restart unless-stopped  \
    -v /home/visioncell/Videos:/Videos \
    -e CLOUD_DOMAIN=$CLOUD_DOMAIN \
    -e AUTH0_DOMAIN=$AUTH0_DOMAIN -e AUTH0_CID=$AUTH0_CID \
    -e REDIS_URL=$REDIS_URL -e REDIS_SERVER=$REDIS_SERVER -e REDIS_PORT=$REDIS_PORT \
    -e DB_NAME=$DB_NAME -e MONGO_SERVER=$MONGO_SERVER -e MONGO_PORT=$MONGO_PORT \
    -e PYTHONUNBUFFERED=1 -e STORE_PATH=/Videos/TimeMachine \
    --log-opt max-size=50m --log-opt max-file=5 \
    -m 10g --cpus="2" \
    --privileged -v /dev:/dev -v /sys:/sys \
    -t fvonprem/x86-eventor:prod

# start rtsp server
sudo docker stop rtsp-server
sudo docker rm rtsp-server
sudo docker run --network=host --name rtsp-server -d --restart unless-stopped \
    --log-opt max-size=50m --log-opt max-file=5 \
    -v $HOME/flex-run/system_server/timemachine/server.yml:/rtsp-simple-server.yml \
    -t fvonprem/x86-rtspserver:prod

# start filesystem servers
chmod +x $HOME/flex-run/scripts/filesystem_server.sh
chmod +x $HOME/flex-run/scripts/mediasystem_server.sh
(sudo crontab -l; echo '@reboot sudo sh '$HOME'/flex-run/scripts/filesystem_server.sh') | sudo crontab -
(sudo crontab -l; echo '@reboot sudo sh '$HOME'/flex-run/scripts/mediasystem_server.sh') | sudo crontab -
sh $HOME/flex-run/scripts/filesystem_server.sh
sh $HOME/flex-run/scripts/mediasystem_server.sh
