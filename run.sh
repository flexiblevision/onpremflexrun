if [ "$EUID" -ne 0 ]
  then echo "Please run as root"
  exit
fi
sudo apt-get update
sudo apt-get install -y python3.6
sh ./scripts/installSwapfile.sh
python3 deploy.py
