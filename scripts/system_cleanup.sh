#sudo find /var/lib/docker/containers/ -type f -name “*.log” -delete

# `docker system prune -f` was too blunt for a device that can roll back, in two
# separate ways:
#
#   1. It removes STOPPED containers. During an upgrade the previous version is
#      stopped and renamed to <name>_prev, and that container IS the rollback
#      target. This job runs @monthly, so firing it mid-swap deleted the only
#      way back.
#   2. It removes dangling (untagged) images. Once a release is pulled by
#      digest the image has no tag, so the previous release's images would be
#      collected and a rollback would need a re-pull over a factory network at
#      exactly the moment a line is already down.
#
# The `until` filters fix both: a _prev container is minutes old and survives,
# and recent images are kept while genuinely old layers are still reclaimed.
sudo /usr/bin/docker container prune -f --filter "until=24h"
sudo /usr/bin/docker image prune -f --filter "until=720h"
sudo /usr/bin/docker network prune -f --filter "until=24h"
sudo /usr/bin/docker builder prune -f --filter "until=720h"

sudo find /var/crash/ -type f -mtime +30 -delete

#clear forever process logs
find /root/.forever/ -type f -name "*.log" -mtime +30 -delete

#clear any efi images
sudo python3 /root/flex-run/scripts/clean_efi.py