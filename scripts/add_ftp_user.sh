useradd -p $(echo $2 | openssl passwd -1 -stdin) -m $1
