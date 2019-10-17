ARCH=$(arch)
if [ "$ARCH" = "aarch64" ]; then
    chmod +x $HOME/flex-run/upgrades/system_container_upgrades.sh
    sh $HOME/flex-run/upgrades/system_container_upgrades.sh $1 $2 $3 'arm'
elif [ "$ARCH" = "x86_64" ]; then
    chmod +x $HOME/flex-run/upgrades/system_container_upgrades.sh
    sh $HOME/flex-run/upgrades/system_container_upgrades.sh $1 $2 $3 'x86'
fi

