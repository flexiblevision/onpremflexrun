apt update -y
docker network create -d bridge imagerie_nw

ARCH=$(arch)
if [ "$ARCH" = "aarch64" ]; then
    sh ./setup/system_setup.sh $1 $2 $3 'arm' $4 $5 $6
elif [ "$ARCH" = "x86_64" ]; then
    sh ./setup/system_setup.sh $1 $2 $3 'x86' $4 $5 $6
fi

chmod +x ./system_server/system_server.sh
sh ./system_server/system_server.sh
