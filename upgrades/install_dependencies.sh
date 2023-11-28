apt install -y vsftpd
apt-get -y install isc-dhcp-server
usermod -aG dialout visioncell

sudo rm /etc/xdg/autostart/update-notifier.desktop
apt-mark hold "nvidia*"
pip3 install 'rq==1.5.0'
pip3 install "boto3==1.26.96"
