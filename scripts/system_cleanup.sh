sudo find /var/lib/docker/containers/ -type f -name “*.log” -delete
sudo /usr/bin/docker system prune -f