#sudo find /var/lib/docker/containers/ -type f -name “*.log” -delete
sudo /usr/bin/docker system prune -f

#clear forever process logs
for i in /root/.forever/*log; do rm $i; done