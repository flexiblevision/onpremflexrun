#sudo find /var/lib/docker/containers/ -type f -name “*.log” -delete
sudo /usr/bin/docker system prune -f

sudo find /var/crash/ -type f -mtime +30 -delete

#clear forever process logs
find /root/.forever/ -type f -name "*.log" -mtime +30 -delete

#clear any efi images
sudo python3 /root/flex-run/scripts/clean_efi.py