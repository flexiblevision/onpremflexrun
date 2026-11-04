#!/bin/sh
# The setup hotspot is up only while the box has no uplink. It shares the radio
# with any Wi-Fi uplink and the two fight for airtime: measured on a VC610 with
# the hotspot up, 31 KB/s and 1.6 s to the router; with it down, 22 MB/s and
# 3 ms. Run every minute from cron.

wifi=
for dev in /sys/class/net/wl*; do
    [ -e "$dev" ] && wifi=${dev##*/} && break
done
[ -n "$wifi" ] || exit 0

has_uplink() {
    gw=$(ip -4 route show default | awk '{print $3; exit}')
    [ -n "$gw" ] && ping -c 3 -W 5 "$gw" >/dev/null 2>&1
}

# --list-running prints "<pid> <wifi iface> (<ap iface>)"
running=$(create_ap --list-running 2>/dev/null | awk -v w="$wifi" '$2 == w {print $1; exit}')

if has_uplink; then
    [ -n "$running" ] || exit 0
    # Someone is setting the box up over the hotspot; leave them on it.
    iw dev ap0 station dump 2>/dev/null | grep -q '^Station' && exit 0
    create_ap --stop "$running"
    exit 0
fi

[ -z "$running" ] || exit 0
SSID="$(jq -r '.ssid' ./fvconfig.json)"
[ -n "$SSID" ] && [ "$SSID" != null ] || exit 0
create_ap --daemon -n "$wifi" "$SSID" password
