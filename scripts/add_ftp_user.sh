useradd -p $(echo $2 | openssl passwd -1 -stdin) -m $1
sudo chown -R $1 /home/ftp