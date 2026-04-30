apt install -y vsftpd
apt-get -y install isc-dhcp-server
apt-get -y install jq
apt-get -y --only-upgrade install google-chrome-stable
apt install -y linux-crashdump kdump-tools 2>/dev/null || echo "Warning: kdump not installed (no apt access) — kernel will still panic+reboot on lockups but won't capture crash dumps"
usermod -aG dialout visioncell

sudo rm /etc/xdg/autostart/update-notifier.desktop

dpkg -l | grep -E '^ii.*nvidia' | awk '{print $2}' | xargs -r apt-mark hold
dpkg -l | grep -E "^ii.*(linux-image|linux-headers)-$(uname -r)" | awk '{print $2}' | xargs -r apt-mark hold
dpkg -l | grep -E '^ii.*(linux-generic|linux-headers-generic|linux-image-generic)' | awk '{print $2}' | xargs -r apt-mark hold

export PYTHONPATH="${PYTHONPATH}:${HOME}/flex-run"

python3 $HOME/flex-run/setup/management.py
pip3 install --break-system-packages --ignore-installed -r $HOME/flex-run/requirements.txt