sed -i "s/\(^$1=\).*/\1$2/" /etc/vsftpd.conf
sudo service vsftpd restart
