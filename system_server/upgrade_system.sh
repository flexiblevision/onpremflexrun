ARCH=$(arch)

sh $HOME/flex-run/upgrades/install_dependencies.sh

if [ "$ARCH" = "aarch64" ]; then
    chmod +x $HOME/flex-run/upgrades/system_container_upgrades.sh
    sh $HOME/flex-run/upgrades/system_container_upgrades.sh $1 $2 $3 'arm' $4 $5
elif [ "$ARCH" = "x86_64" ]; then
    chmod +x $HOME/flex-run/upgrades/system_container_upgrades.sh
    sh $HOME/flex-run/upgrades/system_container_upgrades.sh $1 $2 $3 'x86' $4 $5
fi