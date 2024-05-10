#sudo find /var/lib/docker/containers/ -type f -name “*.log” -delete
sudo /usr/bin/docker system prune -f

sudo rm /var/crash/*

#clear forever process logs
for i in /root/.forever/*log; do rm $i; done

#clear any efi images
sudo python3 /root/flex-run/scripts/clean_efi.py