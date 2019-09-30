apt update -y
apt upgrade -y
docker network create -d bridge imagerie_nw

ARCH=$(arch)
if [ "$ARCH" = "aarch64" ]; then
    sh ../setup/system_setup.sh $1 $2 $3 $ARCH
elif [ "$ARCH" = "x86_64" ]; then
    sh ../setup/system_setup.sh $1 $2 $3 $ARCH
fi

sh ./system_server.sh
