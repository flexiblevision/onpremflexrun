sudo systemctl start vsftpd
sudo systemctl enable vsftpd
sudo ufw allow 20/tcp
sudo ufw allow 21/tcp

sudo mkdir /home/ftp
sudo chmod a+rwx /home/ftp

sudo sed -i "s/\(^write_enable=\).*/\1YES/" /etc/vsftpd.conf
sudo grep -qxF "local_root=/home/ftp" /etc/vsftpd.conf || echo "local_root=/home/ftp" >> /etc/vsftpd.conf
sudo grep -qxF "listen_port=21" /etc/vsftpd.conf || echo "listen_port=21" >> /etc/vsftpd.conf

sudo service vsftpd restart

chmod +x $HOME/flex-run/scripts/start_ftp_server.sh
(sudo crontab -l; echo '@reboot sudo sh '$HOME'/flex-run/scripts/start_ftp_server.sh') | sudo crontab -
forever start -c python3 $HOME/flex-run/system_server/worker_scripts/ftp_worker.py
