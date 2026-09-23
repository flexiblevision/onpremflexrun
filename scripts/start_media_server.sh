# Log to a file, not forever's pipe: see start_fs_server.sh.
exec python3 -m http.server 8001 --directory /media/ > /var/log/fv_media_server.log 2>&1
