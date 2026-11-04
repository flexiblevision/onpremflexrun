# :8001 serves /media. This used to start start_fs_server.sh, so :8001 never ran
# and every call stacked another :8000 monitor.
sh /root/flex-run/scripts/ensure_file_server.sh 8001 /root/flex-run/scripts/start_media_server.sh
