forever stopall

echo "home=$HOME\n$(cat $HOME/flex-run/scripts/fv_system_server_start.sh)" > $HOME/flex-run/scripts/fv_system_server_start.sh
echo "home=$HOME\n$(cat $HOME/flex-run/scripts/worker_server_start.sh)" > $HOME/flex-run/scripts/worker_server_start.sh
echo "home=$HOME\n$(cat $HOME/flex-run/scripts/tcp_server_start.sh)" > $HOME/flex-run/scripts/tcp_server_start.sh
echo "home=$HOME\n$(cat $HOME/flex-run/scripts/gpio_server_start.sh)" > $HOME/flex-run/scripts/gpio_server_start.sh

chmod +x $HOME/flex-run/scripts/fv_system_server_start.sh
chmod +x $HOME/flex-run/scripts/worker_server_start.sh
chmod +x $HOME/flex-run/scripts/redis_server_start.sh
chmod +x $HOME/flex-run/scripts/hotspot.sh
chmod +x $HOME/flex-run/scripts/allocate_usbfs_memory.sh
chmod +x $HOME/flex-run/scripts/restart_localprediction.sh
chmod +x $HOME/flex-run/scripts/tcp_server_start.sh
chmod +x $HOME/flex-run/scripts/gpio_server_start.sh

sudo crontab -r
(sudo crontab -l; echo '@reboot sudo sh '$HOME'/flex-run/scripts/fv_system_server_start.sh') | sudo crontab -
(sudo crontab -l; echo '@reboot sudo sh '$HOME'/flex-run/scripts/redis_server_start.sh') | sudo crontab -
(sudo crontab -l; echo '@reboot sudo sh '$HOME'/flex-run/scripts/tcp_server_start.sh') | sudo crontab -
(sudo crontab -l; echo '@reboot sudo sh '$HOME'/flex-run/scripts/gpio_server_start.sh') | sudo crontab -
(sudo crontab -l; echo '@reboot sudo sh '$HOME'/flex-run/scripts/worker_server_start.sh') | sudo crontab -
(sudo crontab -l; echo '@reboot sleep 30 && sudo  sh '$HOME'/flex-run/scripts/hotspot.sh') | sudo crontab -
(sudo crontab -l; echo '@reboot sudo sh '$HOME'/flex-run/scripts/allocate_usbfs_memory.sh') | sudo crontab -
(sudo crontab -l; echo '@reboot sleep 50 && sudo sh '$HOME'/flex-run/scripts/restart_localprediction.sh') | sudo crontab -

forever start -c python3 $HOME/flex-run/system_server/server.py
forever start -c python3 $HOME/flex-run/system_server/worker.py
forever start -c python3 $HOME/flex-run/system_server/gpio/gpio_controller.py
forever start -c python3 $HOME/flex-run/system_server/tcp/tcp_server.py
forever start -c redis-server --daemonize yes
sudo sh -c 'echo 1000 > /sys/module/usbcore/parameters/usbfs_memory_mb'