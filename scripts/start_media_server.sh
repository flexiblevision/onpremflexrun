# Log to a file, not forever's pipe: see start_fs_server.sh.
exec python3 /root/flex-run/scripts/range_http_server.py 8001 /media/ > /var/log/fv_media_server.log 2>&1
