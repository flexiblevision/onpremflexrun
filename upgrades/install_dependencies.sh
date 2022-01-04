apt install vsftpd
usermod -aG dialout visioncell

sudo grep -qxF 'Hidden=true' /etc/xdg/autostart/update-notifier.desktop || sudo bash -c 'echo "Hidden=true" >> /etc/xdg/autostart/update-notifier.desktop'