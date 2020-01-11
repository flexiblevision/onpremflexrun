git clone --single-branch --branch io-flex-run-branch git://github.com/agoeckel/flex-run.git ~/flex-run-temp
cp -r ~/flex-run-temp/* ~/flex-run/
rm -rf ~/flex-run-temp

sleep 5

sh $HOME/flex-run/upgrades/install_dependencies.sh
sh $HOME/flex-run/upgrades/start_servers.sh