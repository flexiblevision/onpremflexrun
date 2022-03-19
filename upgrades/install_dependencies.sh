apt install vsftpd
usermod -aG dialout visioncell

sudo rm /etc/xdg/autostart/update-notifier.desktop
echo nvidia* hold | dpkg --set-selections