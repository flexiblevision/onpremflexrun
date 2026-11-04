#!/bin/bash
# Builds Realtek's r8127 against the running kernel, which pin_kernel.sh has
# frozen in place. Ubuntu 24.04 only. Exits 0 when there is no card.
set -u

SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)/drivers/r8127"
KVER=$(uname -r)
KDIR=/lib/modules/$KVER/build
STAMP=/var/lib/flexrun/r8127.stamp
BUILD_LOG=/var/log/flexrun-r8127-build.log

log() { echo "r8127: $*" >&2; logger -t flexrun-r8127 "$*" 2>/dev/null || true; }

cards() {
    for dev in /sys/bus/pci/devices/*; do
        # The two IDs r8127's own PCI table claims.
        case "$(cat "$dev/vendor"):$(cat "$dev/device")" in
            0x10ec:0x8127|0x10ec:0x0e10) echo "$dev" ;;
        esac
    done
}

# From 6.16 the in-tree r8169 also matches these, but it only drives the
# SFP+ variant from 7.0, so it must never keep the card.
claim() {
    for dev in $(cards); do
        [ -e "/sys/bus/pci/drivers/r8127/${dev##*/}" ] && continue
        [ -e "$dev/driver" ] && echo "${dev##*/}" > "$dev/driver/unbind"
        echo "${dev##*/}" > /sys/bus/pci/drivers/r8127/bind
    done
}

bound() {
    for dev in $(cards); do
        [ "$(basename "$(readlink "$dev/driver")")" = r8127 ] || return 1
    done
}

[ -n "$(cards)" ] || exit 0

. /etc/os-release
if [ "${VERSION_ID:-}" != "24.04" ]; then
    log "card present but Ubuntu ${VERSION_ID:-unknown} is not supported, only 24.04"
    exit 1
fi
if [ "$(id -u)" -ne 0 ]; then
    log "must run as root"
    exit 1
fi

set -- "$SRC_DIR"/r8127-*.tar.bz2
if [ $# -ne 1 ] || [ ! -f "$1" ]; then
    log "expected exactly one r8127-*.tar.bz2 in $SRC_DIR"
    exit 1
fi
TARBALL=$1
SUM=$(sha256sum "$TARBALL" | cut -d' ' -f1)

if [ "$(cat "$STAMP" 2>/dev/null)" = "$KVER $SUM" ] && modinfo -k "$KVER" r8127 >/dev/null 2>&1; then
    modprobe r8127 && claim
    bound || { log "module installed for $KVER but the card is not bound to it"; exit 1; }
    exit 0
fi

if [ ! -d "$KDIR" ] || ! command -v make >/dev/null; then
    if [ -n "${FLEXRUN_PACKAGES:-}" ] || ! DEBIAN_FRONTEND=noninteractive apt-get install -y "linux-headers-$KVER" make; then
        log "no headers for $KVER and apt is unreachable; connect the box once and rerun"
        exit 1
    fi
fi

# Build with the compiler the kernel was built with, not whatever gcc is the default.
CC=$(grep -oE '[a-z0-9_]+-linux-gnu-gcc-[0-9]+' /proc/version | head -1)
command -v "$CC" >/dev/null 2>&1 || CC=gcc

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
tar -xjf "$TARBALL" -C "$WORK"
SRC=$(find "$WORK" -mindepth 2 -maxdepth 2 -type d -name src -print -quit)
if [ -z "$SRC" ] || ! make -C "$KDIR" M="$SRC" CC="$CC" modules >"$BUILD_LOG" 2>&1; then
    log "build failed against $KVER, see $BUILD_LOG"
    exit 1
fi

if mokutil --sb-state 2>/dev/null | grep -q 'SecureBoot enabled'; then
    KEY=/var/lib/shim-signed/mok/MOK.priv
    CERT=/var/lib/shim-signed/mok/MOK.der
    if [ ! -f "$KEY" ] || ! "$KDIR/scripts/sign-file" sha256 "$KEY" "$CERT" "$SRC/r8127.ko"; then
        log "Secure Boot is on and the module could not be signed with the enrolled MOK key"
        exit 1
    fi
fi

install -D -m 644 "$SRC/r8127.ko" "/lib/modules/$KVER/updates/r8127.ko"
depmod "$KVER"

# r8169 is in the initramfs and loads there, so the ordering has to be in it too.
echo "softdep r8169 pre: r8127" > /etc/modprobe.d/flexrun-r8127.conf
grep -qx r8127 /etc/initramfs-tools/modules || echo r8127 >> /etc/initramfs-tools/modules
update-initramfs -u -k "$KVER"

if ! modprobe r8127; then
    log "modprobe r8127 failed on $KVER, see dmesg"
    exit 1
fi
claim
bound || { log "r8127 loaded but the card is not bound to it"; exit 1; }
mkdir -p "$(dirname "$STAMP")"
echo "$KVER $SUM" > "$STAMP"
log "installed for $KVER"
