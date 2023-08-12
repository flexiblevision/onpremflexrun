redis-cli FLUSHALL
rm /var/lib/redis/*.rdb
forever start -c redis-server --daemonize yes
