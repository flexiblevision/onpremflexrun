# Log to a file, not forever's pipe: if the monitor dies the pipe breaks, and
# the server then fails every request writing its access-log line.
# Range-capable: Time Machine clips have to be seekable (range_http_server.py).
exec python3 /root/flex-run/scripts/range_http_server.py 8000 /home/visioncell/ > /var/log/fv_fs_server.log 2>&1
