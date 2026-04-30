#!/bin/bash
# Sets up GPU health monitoring and hardware watchdog for freeze detection
# No packages required — uses kernel built-in watchdog and a simple logging script
#
# Usage: sudo bash install_gpu_monitor.sh

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: Must run as root"
    exit 1
fi

echo "=== GPU Monitor & Freeze Recovery Setup ==="

# ---- Step 1: GPU health monitor script ----
echo "[1/5] Installing GPU health monitor..."
cat > /usr/local/bin/gpu-health-monitor.sh <<'GPUEOF'
#!/bin/bash
# Logs GPU stats every 60s for post-freeze diagnosis
LOGFILE=/var/log/gpu-health.log

while true; do
    TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")

    # temp, gpu_util%, mem_util%, mem_used_MB, mem_total_MB, power_W, sm_clock_MHz, mem_clock_MHz, ecc_errors
    GPU_DATA=$(nvidia-smi --query-gpu=temperature.gpu,utilization.gpu,utilization.memory,memory.used,memory.total,power.draw,clocks.sm,clocks.mem,ecc.errors.uncorrected.volatile.total --format=csv,noheader,nounits 2>&1)

    if [ $? -ne 0 ]; then
        echo "$TIMESTAMP ERROR: nvidia-smi failed: $GPU_DATA" >> $LOGFILE
    else
        echo "$TIMESTAMP GPU: temp=${GPU_DATA}" >> $LOGFILE
    fi

    # Check for Xid errors in dmesg (GPU fault indicators)
    XID=$(dmesg 2>/dev/null | grep -c "NVRM: Xid" || echo 0)
    if [ "$XID" -gt 0 ]; then
        echo "$TIMESTAMP WARNING: $XID Xid errors detected in dmesg" >> $LOGFILE
        dmesg | grep "NVRM: Xid" | tail -3 >> $LOGFILE
    fi

    # Rotate log at 50MB
    LOGSIZE=$(stat -c%s "$LOGFILE" 2>/dev/null || echo 0)
    if [ "$LOGSIZE" -gt 52428800 ]; then
        mv $LOGFILE ${LOGFILE}.1
    fi

    sleep 60
done
GPUEOF
chmod +x /usr/local/bin/gpu-health-monitor.sh

# ---- Step 2: Systemd service for GPU monitor ----
echo "[2/5] Creating systemd service..."
cat > /etc/systemd/system/gpu-health-monitor.service <<'SVCEOF'
[Unit]
Description=GPU Health Monitor
After=nvidia-persistenced.service

[Service]
Type=simple
ExecStart=/usr/local/bin/gpu-health-monitor.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable --now gpu-health-monitor.service

# ---- Step 3: Kernel panic/lockup settings (idempotent — overwrites each run) ----
echo "[3/5] Applying kernel panic settings..."
cat > /etc/sysctl.d/90-lockup-panic.conf <<'SYSEOF'
kernel.softlockup_panic = 1
kernel.softlockup_all_cpu_backtrace = 1
kernel.hardlockup_panic = 1
kernel.hung_task_panic = 1
kernel.hung_task_timeout_secs = 120
# Panic on kernel Oops (e.g. AppArmor LSM NULL-deref) instead of leaving
# tainted zombies that systemd cannot reap. Combined with kernel.panic = 10,
# the box reboots within ~10s of an Oops instead of degrading silently.
kernel.panic_on_oops = 1
kernel.panic = 10
SYSEOF
sysctl --system

# ---- Step 4: Enable hardware watchdog for freeze recovery ----
echo "[4/5] Enabling hardware watchdog..."

# Load the Intel TCO watchdog module now and on boot
# Ubuntu 24.04 HWE kernel blacklists iTCO_wdt — override it
cat > /etc/modprobe.d/allow-iTCO_wdt.conf <<'MODEOF'
install iTCO_wdt /sbin/modprobe --ignore-install iTCO_wdt
install iTCO_vendor_support /sbin/modprobe --ignore-install iTCO_vendor_support
MODEOF

if ! grep -q iTCO_wdt /etc/modules 2>/dev/null; then
    echo "iTCO_wdt" >> /etc/modules
fi
modprobe iTCO_vendor_support 2>/dev/null || true
modprobe iTCO_wdt 2>/dev/null || true

# Configure watchdog daemon if installed (idempotent — overwrites each run)
if command -v watchdog &>/dev/null; then
    cat > /etc/watchdog.conf <<'WDEOF'
watchdog-device = /dev/watchdog
watchdog-timeout = 15
interval = 5
realtime = yes
priority = 1
max-load-1 = 0
WDEOF
    systemctl enable watchdog 2>/dev/null || true
    systemctl restart watchdog 2>/dev/null || true
    echo "  watchdog daemon configured and started"
else
    echo "  WARNING: watchdog package not installed — install with: apt install -y watchdog"
fi

# Verify /dev/watchdog exists
if [ -e /dev/watchdog ]; then
    echo "  /dev/watchdog found — hardware watchdog active"
else
    echo "  WARNING: /dev/watchdog not found — iTCO_wdt may not be supported on this hardware"
    echo "  The system will NOT auto-reboot on hard freezes"
fi

# ---- Step 5: NVIDIA persistence mode ----
echo "[5/5] Enabling NVIDIA persistence mode..."
nvidia-smi -pm 1 2>/dev/null || true

echo ""
echo "=== Setup complete ==="
echo ""
echo "GPU health log:  /var/log/gpu-health.log"
echo "  Format: timestamp GPU: temp, gpu_util%, mem_util%, mem_used_MB, mem_total_MB, power_W, sm_clock, mem_clock, ecc_errors"
echo ""
echo "Freeze recovery:"
echo "  - Hardware watchdog (iTCO_wdt) forces reboot if system hangs >15s"
echo "  - Kernel NMI watchdog catches soft/hard lockups"
echo "  - Panic settings in /etc/sysctl.d/90-lockup-panic.conf trigger auto-reboot"
echo ""
echo "After a freeze, check:"
echo "  - /var/log/gpu-health.log            (last GPU state before crash)"
echo "  - journalctl -b -1 --reverse | head  (last system logs)"
echo "  - dmesg | grep Xid                   (GPU fault codes)"
