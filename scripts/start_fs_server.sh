# Log to a file, not forever's pipe: if the monitor dies the pipe breaks, and
# http.server then fails every request writing its access-log line.
exec python3 -m http.server 8000 --directory /home/visioncell/ > /var/log/fv_fs_server.log 2>&1
