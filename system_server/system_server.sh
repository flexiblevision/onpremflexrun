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
apt install -y redis-server
apt install -y openssh-server
apt-get -y install isc-dhcp-server
make install -C $HOME/flex-run/scripts/create_ap
npm install forever@3.0.0 -g

pip3 install -r $HOME/flex-run/requirements.txt

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

sudo crontab -r
(sudo crontab -l; echo '@reboot sudo sh '$HOME'/flex-run/scripts/fv_system_server_start.sh') | sudo crontab -
(sudo crontab -l; echo '@reboot sudo sh '$HOME'/flex-run/scripts/redis_server_start.sh') | sudo crontab -
(sudo crontab -l; echo '@reboot sudo sh '$HOME'/flex-run/scripts/tcp_server_start.sh') | sudo crontab -
(sudo crontab -l; echo '@reboot sudo sh '$HOME'/flex-run/scripts/gpio_server_start.sh') | sudo crontab -
(sudo crontab -l; echo '@reboot sudo sh '$HOME'/flex-run/scripts/sync_worker_start.sh') | sudo crontab -

#---workers-----
(sudo crontab -l; echo '@reboot sleep 30 && sudo sh '$HOME'/flex-run/scripts/worker_server_start.sh') | sudo crontab -
#----------------

(sudo crontab -l; echo '@reboot sleep 30 && sudo  sh '$HOME'/flex-run/scripts/hotspot.sh') | sudo crontab -
(sudo crontab -l; echo '@reboot sudo sh '$HOME'/flex-run/scripts/allocate_usbfs_memory.sh') | sudo crontab -
(sudo crontab -l; echo '@reboot sleep 50 && sudo sh '$HOME'/flex-run/scripts/restart_localprediction.sh') | sudo crontab -
(sudo crontab -l; echo '@reboot sudo sh '$HOME'/flex-run/scripts/start_job_watcher.sh') | sudo crontab -
(sudo crontab -l; echo '@monthly sudo sh '$HOME'/flex-run/scripts/system_cleanup.sh') | sudo crontab -
(sudo crontab -l; echo '@reboot sudo sh '$HOME'/flex-run/scripts/filesystem_server.sh') | sudo crontab -
(sudo crontab -l; echo '@reboot sudo sh '$HOME'/flex-run/scripts/mediasystem_server.sh') | sudo crontab -
(sudo crontab -l; echo '0 */8 * * * docker exec vision rm -rf /tmp') | sudo crontab -
(sudo crontab -l; echo '0 0 * * * forever restart '$HOME'/flex-run/system_server/worker_scripts/sync_worker.py') | sudo crontab -
(sudo crontab -l; echo '0 1 * * * rm -rf ~/.cache/google-chrome') | sudo crontab -

forever start -c python3 $HOME/flex-run/system_server/server.py
forever start -c python3 $HOME/flex-run/system_server/worker.py
forever start -c python3 $HOME/flex-run/system_server/tcp/tcp_server.py
forever start -c python3 $HOME/flex-run/system_server/worker_scripts/sync_worker.py
forever start -c python3 $HOME/flex-run/system_server/job_watcher.py

ARCH=$(arch)
if [ "$ARCH" = "x86_64" ]; then
    forever start -c python3 $HOME/flex-run/system_server/gpio/gpio_controller.py
fi

if nvidia-smi --query-gpu=name --format=csv | grep -q 'A4000'; then
    (sudo crontab -l; echo '@reboot sleep 50 && nvidia-smi --lock-gpu-clocks=1500,1500') | sudo crontab -
fi

MAX_MEMORY=10000000000
MAX_MEMORY_POLICY=allkeys-lru
echo "maxmemory $MAX_MEMORY" >> /etc/redis/redis.conf
echo "maxmemory-policy $MAX_MEMORY_POLICY" >> /etc/redis/redis.conf
systemctl restart redis.service

forever start -c redis-server --daemonize yes
sudo sh -c 'echo 1000 > /sys/module/usbcore/parameters/usbfs_memory_mb'
sudo rm /etc/xdg/autostart/update-notifier.desktop
apt-mark hold "nvidia*"