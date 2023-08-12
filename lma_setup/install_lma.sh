SYSTEM_ARCH='x86'
f_path=$HOME"/flex-run/lma_setup"

# allow permissions over socket
sudo chmod 666 /var/run/docker.sock

# install telegraf - system logging
sudo docker run -d --name=telegraf \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v $f_path/telegraf.conf:/etc/telegraf/telegraf.conf:ro \
    -v /:/hostfs:ro \
    --restart unless-stopped \
    -e HOST_ETC=/hostfs/etc \
    -e HOST_PROC=/hostfs/proc \
    -e HOST_SYS=/hostfs/sys \
    -e HOST_VAR=/hostfs/var \
    -e HOST_RUN=/hostfs/run \
    -e HOST_MOUNT_PREFIX=/hostfs \
    -p 8094:8094 \
    fvonprem/$SYSTEM_ARCH-telegraf:prod

# install prometheus - event monitoring
sudo docker run \
    -d --name=prom \
    -p 9090:9090 \
    --network host \
    --restart unless-stopped \
    -v $f_path/prometheus.yml:/etc/prometheus/prometheus.yml \
    fvonprem/$SYSTEM_ARCH-prom:prod

# install cadvisor - metrics 
docker run \
    --volume=/:/rootfs:ro \
    --volume=/var/run:/var/run:rw \
    --volume=/sys:/sys:ro \
    --volume=/var/lib/docker/:/var/lib/docker:ro \
    --volume=/dev/disk/:/dev/disk:ro \
    --publish=8080:8080 \
    --detach=true \
    --name=cadvisor \
    fvonprem/$SYSTEM_ARCH-cadvisor:prod

# install alert manager
sudo docker run \
    -d --name=alert \
    -p 9093:9093 \
    --network host \
    --restart unless-stopped \
    -v $f_path/alerts.yml:/etc/prometheus/alerts.yml \
    prom/alertmanager

# install grafana
docker run -d \
    -p 3033:3033 \
    --network host \
    --name grafana \
    -e "GF_SERVER_HTTP_PORT=3033" \
    fvonprem/$SYSTEM_ARCH:prod