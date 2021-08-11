git clone --single-branch --branch "$(cat ~/flex-run/setup_constants/flex_run_branch.txt)" git://github.com/flexiblevision/onpremflexrun.git ~/flex-run-temp
cp -r ~/flex-run-temp/* ~/flex-run/
rm -rf ~/flex-run-temp

sleep 3

sh $HOME/flex-run/upgrades/system_container_upgrades.sh $1 $2 $3 $4
sh $HOME/flex-run/upgrades/install_dependencies.sh
sh $HOME/flex-run/upgrades/start_servers.sh
