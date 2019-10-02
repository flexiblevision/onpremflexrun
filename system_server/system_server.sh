apt update
apt install -y python3-pip
apt-get -y install nodejs
apt-get -y install npm
apt-get -y install 
npm install forever -g
pip install requests
pip install python-jose
pip install Flask
pip install Flask-RESTful
pip install Flask-Cors
pip install Flask-Jsonpify

chmod 777 ./fv_system_server_start.sh
cp ./fv_system_server_start.sh /etc/init.d/
update-rc.d fv_system_server_start.sh defaults
forever start -c python3 ./server.py

