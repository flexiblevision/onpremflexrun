apt update -y
docker network create -d bridge imagerie_nw

ARCH=$(arch)
if [ "$ARCH" = "aarch64" ]; then
    sh ./setup/system_setup.sh $1 $2 $3 'arm'
elif [ "$ARCH" = "x86_64" ]; then
    sh ./setup/system_setup.sh $1 $2 $3 'x86'
fi

chmod +x ./system_server/system_server.sh
sh ./system_server/system_server.sh
