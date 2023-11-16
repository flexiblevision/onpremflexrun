if [ "$EUID" -ne 0 ]
  then echo "Please run as root"
  exit
fi
sudo apt-get update
sudo apt-get install -y python3.6
sudo apt-get install -y net-tools
sudo apt-get install -y jq

python3 deploy.py
