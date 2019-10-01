ARCH=$(arch)
if [ "$ARCH" = "aarch64" ]; then
    sh ./upgrades/system_container_upgrades.sh $1 $2 $3 'arm'
elif [ "$ARCH" = "x86_64" ]; then
    sh ./upgrades/x86_system_container_upgrades.sh $1 $2 $3 'x86'
fi

