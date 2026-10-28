#!/bin/bash
# Collect diagnostic logs after a system freeze/reboot
# Run this immediately after the system comes back up
# Usage: sudo bash collect_freeze_logs.sh

OUTFILE="/tmp/freeze_diagnostics_$(hostname)_$(date +%Y%m%d_%H%M%S).log"

echo "Collecting freeze diagnostics to $OUTFILE ..."

{
echo "============================================"
echo "FREEZE DIAGNOSTICS - $(hostname)"
echo "Collected: $(date)"
echo "============================================"

echo ""
echo "=== SYSTEM INFO ==="
uname -a
cat /etc/lsb-release 2>/dev/null
uptime

echo ""
echo "=== BOOT HISTORY ==="
last reboot | head -10

echo ""
echo "=== KERNEL TAINT FLAGS ==="
TAINT=$(cat /proc/sys/kernel/tainted)
echo "Taint value: $TAINT"
python3 -c "
t=$TAINT
flags={0:'proprietary module',1:'forced module load',2:'unsafe SMP',3:'forced unload',4:'MCE',5:'bad page',6:'bad taint',7:'ACPI override',8:'warn',9:'staging driver',10:'firmware workaround',11:'out-of-tree',12:'unsigned module',13:'soft lockup',14:'live patch'}
for bit,name in flags.items():
    if t & (1<<bit): print(f'  bit {bit}: {name}')
" 2>/dev/null

echo ""
echo "=== PREVIOUS BOOT: LAST 200 LINES ==="
journalctl -b -1 --no-pager | tail -200

echo ""
echo "=== PREVIOUS BOOT: PANICS / LOCKUPS / ERRORS ==="
journalctl -b -1 --no-pager | grep -i -E 'panic|oops|bug:|call.trace|soft.lockup|hard.lockup|hung_task|watchdog|oom|out of memory|segfault|killed process|I/O error|EXT4-fs|ENOSPC|No space left' | tail -50

echo ""
echo "=== PREVIOUS BOOT: ATH10K / WIFI ==="
journalctl -b -1 --no-pager | grep -i -E 'ath10k|wlp3s0|wlan0|wifi|firmware.crash|firmware.error' | tail -30

echo ""
echo "=== PREVIOUS BOOT: MCE / HARDWARE ERRORS ==="
journalctl -b -1 --no-pager | grep -i -E 'mce|machine check|hardware error|pcie.*error|AER|thermal|critical temp' | tail -20

echo ""
echo "=== PREVIOUS BOOT: USB / PCI ERRORS ==="
journalctl -b -1 --no-pager | grep -i -E 'usb.*error|usb.*disconnect|usb.*reset|overcurrent|undervolt|pci.*error|link.*reset' | tail -20

echo ""
echo "=== PREVIOUS BOOT: DISK / IO ERRORS ==="
journalctl -b -1 --no-pager | grep -i -E 'I/O error|EXT4|sda|blk_update|Buffer I/O|ENOSPC|No space left|readonly' | tail -20

echo ""
echo "=== PREVIOUS BOOT: OOM KILLER ==="
journalctl -b -1 --no-pager | grep -i -E 'oom|out of memory|killed process|invoked oom' | tail -20

echo ""
echo "=== PREVIOUS BOOT: GPU / NVIDIA ==="
journalctl -b -1 --no-pager | grep -i -E 'nvidia|gpu|nvrm|xid' | tail -20

echo ""
echo "=== PREVIOUS BOOT: RDP / REMOTE DESKTOP ==="
journalctl -b -1 --no-pager | grep -i -E 'gnome-remote-desktop|grdctl|grd-|freerdp|rdp|mutter.*fail|page flip|compositor.*fail|pipewire.*error|framebuffer.*fail|gbm_bo' | tail -30

echo ""
echo "=== RDP SERVICES (current) ==="
systemctl list-units --all 2>/dev/null | grep -iE 'remote-desktop|grd'
ps aux 2>/dev/null | grep -iE 'grd|remote-desktop|freerdp|xrdp' | grep -v grep

echo ""
echo "=== CURRENT BOOT: DMESG ERRORS ==="
dmesg | grep -i -E 'error|fail|panic|lockup|hung|oom|mce|ath10k' | tail -40

echo ""
echo "=== DISK USAGE ==="
df -h
echo ""
du -h --max-depth=1 /var/lib 2>/dev/null | sort -rh | head -10

echo ""
echo "=== MEMORY ==="
free -h

echo ""
echo "=== WIFI STATUS ==="
lsmod | grep ath10k
ip link show | grep -i wl
iwconfig 2>/dev/null | grep -A2 'wl'
cat /etc/NetworkManager/conf.d/no-powersave.conf 2>/dev/null || echo "no-powersave.conf NOT FOUND"

echo ""
echo "=== SYSCTL LOCKUP SETTINGS ==="
sysctl kernel.softlockup_panic kernel.hardlockup_panic kernel.hung_task_panic kernel.panic kernel.nmi_watchdog 2>/dev/null

echo ""
echo "=== DOCKER STATUS ==="
docker ps -a --format "table {{.Names}}\t{{.Status}}" 2>/dev/null

echo ""
echo "=== FOREVER STATUS ==="
forever list 2>/dev/null

echo ""
echo "=== NETWORK ROUTES ==="
ip route show

echo ""
echo "=== SMART DISK HEALTH ==="
smartctl -H /dev/sda 2>/dev/null || echo "smartctl not installed"

echo ""
echo "============================================"
echo "END OF DIAGNOSTICS"
echo "============================================"

} > "$OUTFILE" 2>&1

echo "Done. Logs saved to: $OUTFILE"
echo "To review: cat $OUTFILE"
echo "To send: copy $OUTFILE to your workstation"
