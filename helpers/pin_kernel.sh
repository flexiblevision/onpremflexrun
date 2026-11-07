#!/bin/bash
# Freezes whatever kernel the box is running. Runs on every install and upgrade,
# so it must stay idempotent.
set -u

KVER=$(uname -r)
KABI=${KVER%-generic}

dpkg -l | grep -E '^ii.*nvidia' | awk '{print $2}' | xargs -r apt-mark hold
dpkg -l | grep -E "^ii.*(linux-image|linux-headers)-${KVER}" | awk '{print $2}' | xargs -r apt-mark hold
dpkg -l | grep -E '^ii.*(linux-generic|linux-headers-generic|linux-image-generic)' | awk '{print $2}' | xargs -r apt-mark hold

. /etc/os-release
[ "${VERSION_ID:-}" = "24.04" ] || exit 0
if [ "$(id -u)" -ne 0 ]; then
    echo "pin_kernel: must run as root to freeze $KVER" >&2
    exit 1
fi

# Holds only stop upgrades. This also refuses any other kernel ABI that an
# explicit install or a dependency would pull in. apt takes the first matching
# record, so the running kernel's allow stanza must come first.
cat > /etc/apt/preferences.d/flexrun-kernel-freeze <<EOF
Package: linux-image-${KVER} linux-image-unsigned-${KVER} linux-modules-${KVER} linux-modules-extra-${KVER} linux-headers-${KVER} linux-headers-${KABI}
Pin: version *
Pin-Priority: 500

Package: /^linux-(image|image-unsigned|modules|modules-extra|headers)-[0-9]/
Pin: version *
Pin-Priority: -1
EOF

# Without this, a kernel that gets installed anyway becomes the default boot entry.
ROOT_UUID=$(findmnt -no UUID /)
ENTRY_ID="gnulinux-${KVER}-advanced-${ROOT_UUID}"
GRUB_ENTRY="gnulinux-advanced-${ROOT_UUID}>${ENTRY_ID}"
if [ -z "$ROOT_UUID" ] || ! grep -q "'${ENTRY_ID}'" /boot/grub/grub.cfg 2>/dev/null; then
    echo "pin_kernel: no grub entry for $KVER, boot default left unchanged" >&2
elif ! grep -qxF "GRUB_DEFAULT=\"${GRUB_ENTRY}\"" /etc/default/grub; then
    sed -i "s|^GRUB_DEFAULT=.*|GRUB_DEFAULT=\"${GRUB_ENTRY}\"|" /etc/default/grub
    update-grub
fi
