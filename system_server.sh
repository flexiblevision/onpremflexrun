apt update
apt install -y python3-pip
apt-get -y install nodejs
apt-get -y install npm
npm install forever -g
pip install Flask
pip install Flask-RESTful
pip install Flask-Cors
pip install Flask-Jsonpify
forever start -c python ./system_server/server.py
