apt install vsftpd
usermod -aG dialout visioncell

sudo rm /etc/xdg/autostart/update-notifier.desktop
apt-mark hold "nvidia*"
pip3 install 'rq==1.5.0'