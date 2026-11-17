#!/bin/bash
# Undoes the freeze-debugging setup older releases installed: panic-on-lockup
# sysctls, kdump, the GPU health monitor and the iTCO hardware watchdog. On
# lightweight boxes they turned slow installs into reboots. Idempotent, and
# never fails the caller.

log() { echo "remove-freeze-tooling: $*" >&2; logger -t flexrun-freeze-tooling "$*" 2>/dev/null || true; }

if [ "$(id -u)" -ne 0 ]; then
    log "must run as root; skipped"
    exit 0
fi
export DEBIAN_FRONTEND=noninteractive

installed() { dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q '^install ok installed'; }

if [ -f /etc/sysctl.d/90-lockup-panic.conf ]; then
    rm -f /etc/sysctl.d/90-lockup-panic.conf
    # Kernel defaults, so the running box stops panicking without a reboot.
    sysctl -q -w kernel.softlockup_panic=0 kernel.softlockup_all_cpu_backtrace=0 \
        kernel.hardlockup_panic=0 kernel.hung_task_panic=0 \
        kernel.hung_task_timeout_secs=120 kernel.panic_on_oops=0 kernel.panic=0 2>/dev/null
    sysctl -q --system >/dev/null 2>&1
    log "removed lockup panic settings"
fi

if [ -f /etc/systemd/system/gpu-health-monitor.service ]; then
    systemctl disable --now gpu-health-monitor.service 2>/dev/null
    rm -f /etc/systemd/system/gpu-health-monitor.service /usr/local/bin/gpu-health-monitor.sh
    rm -f /var/log/gpu-health.log /var/log/gpu-health.log.1
    systemctl daemon-reload
    log "removed gpu health monitor"
fi

if installed watchdog; then
    # With nowayout the timer cannot be stopped, so stopping the daemon (purge
    # does) would reboot the box. Disable it now; the next run purges it.
    if [ "$(cat /sys/module/iTCO_wdt/parameters/nowayout 2>/dev/null)" = Y ]; then
        systemctl disable watchdog wd_keepalive 2>/dev/null
        log "watchdog disabled; purged on the first run after a reboot"
    else
        systemctl disable --now watchdog wd_keepalive 2>/dev/null
        apt-get purge -y watchdog >/dev/null 2>&1 || log "could not purge watchdog"
        log "removed watchdog daemon"
    fi
fi
rm -f /etc/modprobe.d/allow-iTCO_wdt.conf
sed -i '/^iTCO_wdt$/d' /etc/modules 2>/dev/null

GRUB_KDUMP=/etc/default/grub.d/kdump-tools.cfg
had_crashkernel=0
[ -f "$GRUB_KDUMP" ] && had_crashkernel=1
for pkg in linux-crashdump kdump-tools kexec-tools; do
    installed "$pkg" && { apt-get purge -y "$pkg" >/dev/null 2>&1 || log "could not purge $pkg"; }
done
# The crashkernel= reservation holds back RAM until GRUB stops passing it.
if [ "$had_crashkernel" = 1 ]; then
    rm -f "$GRUB_KDUMP"
    update-grub >/dev/null 2>&1 || log "update-grub failed; crashkernel reservation stays until it runs"
    log "removed kdump and its memory reservation (frees after reboot)"
fi
exit 0
