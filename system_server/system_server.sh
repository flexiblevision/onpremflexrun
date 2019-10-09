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
pip install redis
pip install rq

echo "home=$HOME\n$(cat $HOME/flex-run/scripts/fv_system_server_start.sh)" > $HOME/flex-run/scripts/fv_system_server_start.sh
chmod +x ../scripts/fv_system_server_start.sh
echo '@reboot sudo sh '$HOME'/flex-run/scripts/fv_system_server_start.sh' | sudo crontab -u root -
forever start -c python3 ./server.py

