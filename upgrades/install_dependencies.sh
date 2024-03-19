apt install -y vsftpd
apt-get -y install isc-dhcp-server
apt-get -y install jq
usermod -aG dialout visioncell

sudo rm /etc/xdg/autostart/update-notifier.desktop
apt-mark hold "nvidia*"

pip3 install -r $HOME/flex-run/requirements.txt