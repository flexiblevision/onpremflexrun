# Shared deploy helpers. Resolved relative to this script, which the setup path
# invokes as ./system_server/system_server.sh from the repo root; $HOME is the
# fallback for an installed tree.
for _lib in "$(dirname "$0")/../upgrades/lib/deploy_common.sh" \
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

apt update
apt install -y python3-pip
apt install -y vim
apt install -y vsftpd
apt install -y net-tools
apt-get -y install jq
apt-get -y install nodejs
apt-get -y install npm
apt-get -y install curl
apt-get -y install hostapd
apt install -y redis-server
apt install -y openssh-server
apt-get -y install isc-dhcp-server
apt install -y linux-crashdump kdump-tools 2>/dev/null || echo "Warning: kdump not installed (no apt access) — kernel will still panic+reboot on lockups but won't capture crash dumps"
make install -C $HOME/flex-run/scripts/create_ap
npm install forever@3.0.0 -g

# --break-system-packages only exists on pip >= 23.0 (PEP 668). Older pip both
# rejects the flag and doesn't need it, so only pass it when supported.
PIP_BSP=""
if pip3 install --help 2>/dev/null | grep -q -- --break-system-packages; then
    PIP_BSP="--break-system-packages"
fi
pip3 install $PIP_BSP --ignore-installed -r $HOME/flex-run/requirements.txt

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
chmod +x $HOME/flex-run/scripts/system_cleanup.sh
chmod +x $HOME/flex-run/scripts/filesystem_server.sh
chmod +x $HOME/flex-run/scripts/mediasystem_server.sh
chmod +x $HOME/flex-run/scripts/configure_network.sh

sudo $HOME/flex-run/scripts/configure_network.sh

# Enable kernel panic on lockups so kdump captures a crash dump instead of silent hang
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
# Do NOT panic on kernel Oops — a flaky driver (e.g. nvidia) oopsing would
# otherwise force a full reboot. The kernel kills the offending task and keeps
# running (accepted risk: occasional tainted zombies systemd cannot reap).
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

# Enable kdump to write crash dumps on panic (if installed)
if [ -f /etc/default/kdump-tools ]; then
    sed -i 's/^USE_KDUMP=.*/USE_KDUMP=1/' /etc/default/kdump-tools
    systemctl enable kdump-tools 2>/dev/null || true
fi

# Root crontab, installed atomically from the shared block in
# upgrades/lib/deploy_common.sh (same block the upgrade path uses). This
# replaces 19 sequential read-modify-write calls preceded by `crontab -r`.
install_crontab

forever start -c python3 $HOME/flex-run/system_server/server.py
forever start -c python3 $HOME/flex-run/system_server/worker.py
forever start -c python3 $HOME/flex-run/system_server/tcp/tcp_server.py
forever start -c python3 $HOME/flex-run/system_server/worker_scripts/sync_worker.py
forever start -c python3 $HOME/flex-run/system_server/job_watcher.py

ARCH=$(arch)
if [ "$ARCH" = "x86_64" ]; then
    forever start -c python3 $HOME/flex-run/system_server/gpio/gpio_controller.py
fi

configure_redis

forever start -c redis-server --daemonize yes
sudo sh -c 'echo 1000 > /sys/module/usbcore/parameters/usbfs_memory_mb'
sudo rm /etc/xdg/autostart/update-notifier.desktop
# Hold installed nvidia packages and kernel to prevent mismatched updates breaking GPU drivers
dpkg -l | grep -E '^ii.*nvidia' | awk '{print $2}' | xargs -r apt-mark hold
dpkg -l | grep -E "^ii.*(linux-image|linux-headers)-$(uname -r)" | awk '{print $2}' | xargs -r apt-mark hold
# Hold kernel metapackages to prevent new kernel versions from being pulled in
dpkg -l | grep -E '^ii.*(linux-generic|linux-headers-generic|linux-image-generic)' | awk '{print $2}' | xargs -r apt-mark hold