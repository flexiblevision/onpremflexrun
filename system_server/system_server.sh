# Shared deploy helpers. Resolved relative to this script, which the setup path
# invokes as ./system_server/system_server.sh from the repo root; $HOME is the
# fallback for an installed tree.
for _lib in "$(dirname "$0")/../upgrades/lib/deploy_common.sh" \
            "$HOME/flex-run/upgrades/lib/deploy_common.sh"; do
    if [ -r "$_lib" ]; then
        . "$_lib"
        _lib_loaded=1
        break
    fi
done
if [ -z "${_lib_loaded:-}" ]; then
    echo "ERROR: cannot find upgrades/lib/deploy_common.sh - deploy tree is incomplete" >&2
    exit 1
fi

apt update
apt install -y python3-pip
apt install -y vim
apt install -y vsftpd
apt install -y net-tools
apt-get -y install jq
apt-get -y install nodejs
apt-get -y install npm
apt-get -y install curl
apt-get -y install hostapd
apt-get -y install iw
apt install -y redis-server
apt install -y openssh-server
apt-get -y install isc-dhcp-server
make install -C $HOME/flex-run/scripts/create_ap
npm install forever@3.0.0 -g

# --break-system-packages only exists on pip >= 23.0 (PEP 668). Older pip both
# rejects the flag and doesn't need it, so only pass it when supported.
PIP_BSP=""
if pip3 install --help 2>/dev/null | grep -q -- --break-system-packages; then
    PIP_BSP="--break-system-packages"
fi
pip3 install $PIP_BSP --ignore-installed -r $HOME/flex-run/requirements.txt
# A box set up before has the old versions' metadata beside the new ones.
python3 $HOME/flex-run/deploy.py --repair-deps

chmod +x $HOME/flex-run/scripts/fv_system_server_start.sh
chmod +x $HOME/flex-run/scripts/worker_server_start.sh
chmod +x $HOME/flex-run/scripts/redis_server_start.sh
chmod +x $HOME/flex-run/scripts/hotspot.sh
chmod +x $HOME/flex-run/scripts/allocate_usbfs_memory.sh
chmod +x $HOME/flex-run/scripts/restart_localprediction.sh
chmod +x $HOME/flex-run/scripts/tcp_server_start.sh
chmod +x $HOME/flex-run/scripts/gpio_server_start.sh
chmod +x $HOME/flex-run/scripts/sync_worker_start.sh
chmod +x $HOME/flex-run/scripts/start_job_watcher.sh
chmod +x $HOME/flex-run/scripts/system_cleanup.sh
chmod +x $HOME/flex-run/scripts/filesystem_server.sh
chmod +x $HOME/flex-run/scripts/mediasystem_server.sh
chmod +x $HOME/flex-run/scripts/configure_network.sh

sudo $HOME/flex-run/scripts/configure_network.sh

# Enable persistent journal so crash logs survive reboot
mkdir -p /var/log/journal
systemd-tmpfiles --create --prefix /var/log/journal
systemctl restart systemd-journald

# Disable WiFi power save to prevent ath10k_pci (QCA6174) kernel lockups
printf '[connection]\nwifi.powersave = 2\n' > /etc/NetworkManager/conf.d/no-powersave.conf

# Root crontab, installed atomically from the shared block in
# upgrades/lib/deploy_common.sh (same block the upgrade path uses). This
# replaces 19 sequential read-modify-write calls preceded by `crontab -r`.
install_crontab

forever start -c python3 $HOME/flex-run/system_server/server.py
forever start -c python3 $HOME/flex-run/system_server/worker.py
forever start -c python3 $HOME/flex-run/system_server/tcp/tcp_server.py
forever start -c python3 $HOME/flex-run/system_server/worker_scripts/sync_worker.py
forever start -c python3 $HOME/flex-run/system_server/job_watcher.py

ARCH=$(arch)
if [ "$ARCH" = "x86_64" ]; then
    forever start -c python3 $HOME/flex-run/system_server/gpio/gpio_controller.py
fi

configure_redis

forever start -c redis-server --daemonize yes
sudo sh -c 'echo 1000 > /sys/module/usbcore/parameters/usbfs_memory_mb'
sudo rm /etc/xdg/autostart/update-notifier.desktop
bash "$(dirname "$0")/../helpers/remove_freeze_tooling.sh"
bash "$(dirname "$0")/../helpers/pin_kernel.sh"
bash "$(dirname "$0")/../helpers/install_touchscreen_pin.sh"
# Last, so a failed driver build is the status setup reports.
bash "$(dirname "$0")/../helpers/install_r8127.sh"