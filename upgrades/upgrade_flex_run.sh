git clone --single-branch --branch "$(jq '.branch' ~/fvconfig.json)" https://github.com/flexiblevision/onpremflexrun.git ~/flex-run-temp
cp -r ~/flex-run-temp/* ~/flex-run/
rm -rf ~/flex-run-temp

sleep 3