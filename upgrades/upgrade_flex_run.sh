git clone --single-branch --branch "$(cat ~/flex-run/setup_constants/flex_run_branch.txt)" git://github.com/flexiblevision/onpremflexrun.git ~/flex-run-temp
cp -r ~/flex-run-temp/* ~/flex-run/
rm -rf ~/flex-run-temp

sleep 3