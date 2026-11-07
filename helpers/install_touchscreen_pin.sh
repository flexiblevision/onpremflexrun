#!/bin/bash
# Installs pin_touchscreen.py and runs it at every GNOME login. Never fails the
# caller: it is the last line of system_server.sh, whose status setup reports.

log() { echo "touchscreen-pin: $*" >&2; logger -t flexrun-touchscreen-pin "$*" 2>/dev/null || true; }

if [ "$(id -u)" -ne 0 ]; then
    log "must run as root; skipped"
    exit 0
fi

install -m 0755 "$(dirname "$0")/pin_touchscreen.py" /usr/local/bin/fv-pin-touchscreen \
    || { log "could not install /usr/local/bin/fv-pin-touchscreen"; exit 0; }

cat > /etc/xdg/autostart/fv-pin-touchscreen.desktop <<'EOF' || log "could not write autostart entry"
[Desktop Entry]
Type=Application
Name=FV pin touchscreen
Exec=/usr/local/bin/fv-pin-touchscreen
NoDisplay=true
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Phase=Application
EOF
exit 0
