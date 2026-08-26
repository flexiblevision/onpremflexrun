# Shared deploy helpers. Resolved relative to this script so it works whether
# we were invoked by path ($HOME/flex-run/upgrades/start_servers.sh from the
# upgrade) or from a repo checked out elsewhere; $HOME is the fallback.
for _lib in "$(dirname "$0")/lib/deploy_common.sh" \
            "$HOME/flex-run/upgrades/lib/deploy_common.sh"; do
    if [ -r "$_lib" ]; then
        . "$_lib"
        _lib_loaded=1
        break
    fi
done
if [ -z "${_lib_loaded:-}" ]; then
    echo "ERROR: cannot find upgrades/lib/deploy_common.sh - deploy tree is incomplete" >&2
    exit 1
fi

chmod +x $HOME/flex-run/scripts/fv_system_server_start.sh
chmod +x $HOME/flex-run/scripts/worker_server_start.sh
chmod +x $HOME/flex-run/scripts/redis_server_start.sh
chmod +x $HOME/flex-run/scripts/hotspot.sh
chmod +x $HOME/flex-run/scripts/allocate_usbfs_memory.sh
chmod +x $HOME/flex-run/scripts/restart_localprediction.sh
chmod +x $HOME/flex-run/scripts/tcp_server_start.sh
chmod +x $HOME/flex-run/scripts/gpio_server_start.sh
chmod +x $HOME/flex-run/scripts/sync_worker_start.sh
chmod +x $HOME/flex-run/scripts/start_job_watcher.sh
chmod +x $HOME/flex-run/scripts/start_ftp_server.sh
chmod +x $HOME/flex-run/scripts/system_cleanup.sh
chmod +x $HOME/flex-run/scripts/filesystem_server.sh
chmod +x $HOME/flex-run/scripts/mediasystem_server.sh
chmod +x $HOME/flex-run/scripts/configure_network.sh

sudo $HOME/flex-run/scripts/configure_network.sh

# Ensure kernel panic on lockups (idempotent — overwrites if already present)
cat > /etc/sysctl.d/90-lockup-panic.conf <<'EOF'
# Soft lockups are often transient under heavy GPU/vision/I-O load, so we do NOT
# panic on them (avoids false-positive reboots) — but still capture all-CPU
# backtraces for diagnostics.
kernel.softlockup_panic = 0
kernel.softlockup_all_cpu_backtrace = 1
# Hard lockups are genuine (CPU stuck with IRQs off) — panic.
kernel.hardlockup_panic = 1
# Hung tasks: timeout raised to 300s so slow disk/USB/fsync don't trip a false hang.
kernel.hung_task_panic = 1
kernel.hung_task_timeout_secs = 300
# Do NOT panic on kernel Oops — avoids full reboots from a flaky driver oopsing.
kernel.panic_on_oops = 0
kernel.panic = 10
EOF
sysctl --system

# Enable persistent journal so crash logs survive reboot
mkdir -p /var/log/journal
systemd-tmpfiles --create --prefix /var/log/journal
systemctl restart systemd-journald

# Disable WiFi power save to prevent ath10k_pci (QCA6174) kernel lockups
printf '[connection]\nwifi.powersave = 2\n' > /etc/NetworkManager/conf.d/no-powersave.conf

# Root crontab, installed atomically from the shared block in
# upgrades/lib/deploy_common.sh (same block the setup path uses).
install_crontab

#restart worker server
forever stop $HOME/flex-run/system_server/worker.py
forever start -c python3 $HOME/flex-run/system_server/worker.py

forever stop $HOME/flex-run/system_server/worker_scripts/sync_worker.py
forever start -c python3 $HOME/flex-run/system_server/worker_scripts/sync_worker.py

forever stop $HOME/flex-run/system_server/tcp/tcp_server.py
forever start -c python3 $HOME/flex-run/system_server/tcp/tcp_server.py

forever stop $HOME/flex-run/system_server/job_watcher.py
forever start -c python3 $HOME/flex-run/system_server/job_watcher.py

ARCH=$(arch)
if [ "$ARCH" = "x86_64" ]; then
    forever stop $HOME/flex-run/system_server/gpio/gpio_controller.py
    forever start -c python3 $HOME/flex-run/system_server/gpio/gpio_controller.py
fi

if [ -e /etc/vsftpd.conf ]
then
    forever stop $HOME/flex-run/system_server/ftp_worker.py
    forever start -c python3 $HOME/flex-run/system_server/ftp_worker.py
else
    echo "ftp config not found"
fi

configure_redis
sleep 3

forever stop $HOME/flex-run/system_server/server.py
sleep 2
forever start -c python3 $HOME/flex-run/system_server/server.py
