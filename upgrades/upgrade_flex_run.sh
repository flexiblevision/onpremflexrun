git clone --single-branch --branch "$(cat ../flex_run_branch.txt)" git://github.com/flexiblevision/onpremflexrun.git ~/flex-run-temp
cp -r ~/flex-run-temp/* ~/flex-run/
rm -rf ~/flex-run-temp

sleep 3

sh $HOME/flex-run/upgrades/install_dependencies.sh
sh $HOME/flex-run/upgrades/start_servers.sh
