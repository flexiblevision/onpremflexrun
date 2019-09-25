apt update -y
apt upgrade -y
# apt remove -y docker docker-engine docker.io containerd runc
# apt update -y
# apt install -y docker.io
# systemctl start docker
# systemctl enable docker
# docker volume ls -q -f driver=nvidia-docker | xargs -r -I{} -n1 docker ps -q -a -f volume={} | xargs -r docker rm -f
# curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | \
#   apt-key add -
# distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
# curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.list | \
#   tee /etc/apt/sources.list.d/nvidia-docker.list
# apt update
# apt-get install -y nvidia-docker2
# pkill -SIGHUP dockerd
# docker run --runtime=nvidia --rm nvidia/cuda:9.0-base nvidia-smi
# usermod -aG docker $USER
# systemctl enable docker

docker network create -d bridge imagerie_nw
#docker pull minio/minio
#docker pull tensorflow/serving:1.12.0-gpu

docker run -d --name=capdev -p 0.0.0.0:5000:5000 --restart unless-stopped --privileged -v /dev:/dev -v /sys:/sys \
    --network imagerie_nw -e ACCESS_KEY=imagerie -e SECRET_KEY=imagerie \
    -d agoeckel/prod-onprem-backend:latest 

docker run -p 0.0.0.0:80:3000 --restart unless-stopped \
    --name captureui -e CAPTURE_SERVER=http://capdev:5000 -e PROCESS_SERVER=http://capdev -d --network imagerie_nw \
     agoeckel/prod-onprem-frontend:latest

docker run -p 8500:8500 -p 8501:8501 --runtime=nvidia --name localprediction  -d -e AWS_ACCESS_KEY_ID=imagerie -e AWS_SECRET_ACCESS_KEY=imagerie -e AWS_REGION=us-east-1 \
    --restart unless-stopped --network imagerie_nw  \
    -t agoeckel/onprem-localprediction:latest


sh ./system_server.sh
