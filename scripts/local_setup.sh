apt update -y
apt upgrade -y
docker network create -d bridge imagerie_nw

ARCH=$(arch)
if [ "$ARCH" = "aarch64" ]; then
    sh ../setup/arm_system_setup.sh
elif [ "$ARCH" = "x86_64" ]; then
    sh ../setup/x86_system_setup.sh
fi

sh ./system_server.sh
