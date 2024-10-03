apt install -y vsftpd
apt-get -y install isc-dhcp-server
apt-get -y install jq
apt-get -y --only-upgrade install google-chrome-stable
usermod -aG dialout visioncell

sudo rm /etc/xdg/autostart/update-notifier.desktop
apt-mark hold "nvidia*"

export PYTHONPATH="${PYTHONPATH}:${HOME}/flex-run"

python3 $HOME/flex-run/setup/management.py
pip3 install -r $HOME/flex-run/requirements.txt