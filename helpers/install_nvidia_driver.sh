#!/bin/bash
# Install closed-source (proprietary) NVIDIA driver on Ubuntu 24.04
# Replaces the open kernel module with the more stable proprietary variant
#
# Usage: sudo bash install_nvidia_driver.sh [DRIVER_VERSION]
#   DRIVER_VERSION: e.g. 580 (default), 570, 535

set -euo pipefail

DRIVER_VERSION="${1:-580}"
RUNNING_KERNEL=$(uname -r)
KERNEL_FLAVOUR=$(echo "$RUNNING_KERNEL" | grep -oP '\d+\.\d+' | head -1)

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: Must run as root"
    exit 1
fi

echo "=== NVIDIA Proprietary Driver Installer ==="
echo "Driver version: $DRIVER_VERSION"
echo "Running kernel: $RUNNING_KERNEL"
echo "Kernel series:  $KERNEL_FLAVOUR"
echo ""

# ---- Step 1: Unhold all nvidia and kernel packages ----
echo "[1/7] Unholding nvidia and kernel packages..."
apt-mark showhold | grep -E 'nvidia|linux-modules-nvidia' | xargs -r apt-mark unhold
echo "  Done."

# ---- Step 2: Update package cache ----
echo "[2/7] Updating package cache..."
apt-get update -qq

# ---- Step 3: Remove open kernel module packages ----
echo "[3/7] Removing open nvidia kernel module packages..."
# Collect all installed open module packages
OPEN_PKGS=$(dpkg -l | grep "nvidia-${DRIVER_VERSION}-open" | grep -E '^ii|^iU|^iF|^hi' | awk '{print $2}' || true)
OPEN_META=$(dpkg -l | grep "nvidia-driver-${DRIVER_VERSION}-open\|nvidia-kernel-source-${DRIVER_VERSION}-open" | grep -E '^ii|^iU|^iF|^hi' | awk '{print $2}' || true)

if [ -n "$OPEN_META" ] || [ -n "$OPEN_PKGS" ]; then
    # Remove metapackages first, then modules
    for pkg in $OPEN_META; do
        echo "  Removing $pkg..."
        dpkg --force-depends --remove "$pkg" 2>/dev/null || true
    done
    for pkg in $OPEN_PKGS; do
        echo "  Removing $pkg..."
        dpkg --force-depends --remove "$pkg" 2>/dev/null || true
    done
else
    echo "  No open module packages found."
fi

# ---- Step 4: Remove any broken OEM kernel module packages ----
echo "[4/7] Cleaning up broken OEM nvidia module packages..."
BROKEN_PKGS=$(dpkg -l | grep "linux-modules-nvidia-${DRIVER_VERSION}.*oem" | grep -E '^iF|^iU|^iH' | awk '{print $2}' || true)
for pkg in $BROKEN_PKGS; do
    echo "  Removing broken package: $pkg"
    dpkg --force-depends --remove "$pkg" 2>/dev/null || true
done

# Fix any remaining dpkg issues
dpkg --configure -a 2>/dev/null || true
apt-get -f install -y 2>/dev/null || true

# ---- Step 5: Install proprietary driver and kernel modules ----
echo "[5/7] Installing proprietary nvidia-driver-${DRIVER_VERSION}..."

# Install the kernel-specific module package first (avoids pulling in modules for kernels we don't have)
KERNEL_MOD_PKG="linux-modules-nvidia-${DRIVER_VERSION}-${RUNNING_KERNEL}"
if apt-cache show "$KERNEL_MOD_PKG" &>/dev/null; then
    echo "  Installing $KERNEL_MOD_PKG..."
    apt-get install -y "$KERNEL_MOD_PKG" 2>&1 | tail -3
else
    echo "  WARNING: $KERNEL_MOD_PKG not found in repos"
fi

# Install the kernel-series metapackage
KERNEL_META_PKG="linux-modules-nvidia-${DRIVER_VERSION}-generic-${KERNEL_FLAVOUR}"
if apt-cache show "$KERNEL_META_PKG" &>/dev/null; then
    echo "  Installing $KERNEL_META_PKG..."
    apt-get install -y "$KERNEL_META_PKG" 2>&1 | tail -3
else
    echo "  WARNING: $KERNEL_META_PKG not found, trying generic-hwe-24.04..."
    apt-get install -y "linux-modules-nvidia-${DRIVER_VERSION}-generic-hwe-24.04" 2>&1 | tail -3
fi

# Install the driver metapackage and proprietary kernel source
apt-get install -y "nvidia-driver-${DRIVER_VERSION}" "nvidia-kernel-source-${DRIVER_VERSION}" 2>&1 | tail -5

# ---- Step 6: Verify installation ----
echo "[6/7] Verifying installation..."
INSTALLED_DRIVER=$(dpkg -l "nvidia-driver-${DRIVER_VERSION}" 2>/dev/null | grep '^ii' | awk '{print $3}')
INSTALLED_KSRC=$(dpkg -l "nvidia-kernel-source-${DRIVER_VERSION}" 2>/dev/null | grep '^ii' | awk '{print $3}')
KERNEL_MODS=$(find "/lib/modules/${RUNNING_KERNEL}/kernel/nvidia-${DRIVER_VERSION}/" -name 'nvidia.ko' 2>/dev/null)

if [ -z "$INSTALLED_DRIVER" ]; then
    echo "  ERROR: nvidia-driver-${DRIVER_VERSION} not installed!"
    exit 1
fi

if [ -z "$INSTALLED_KSRC" ]; then
    echo "  ERROR: nvidia-kernel-source-${DRIVER_VERSION} (proprietary) not installed!"
    exit 1
fi

# Check that we DON'T have the open kernel source
OPEN_KSRC=$(dpkg -l "nvidia-kernel-source-${DRIVER_VERSION}-open" 2>/dev/null | grep '^ii' || true)
if [ -n "$OPEN_KSRC" ]; then
    echo "  WARNING: Open kernel source still installed — removing..."
    dpkg --force-depends --remove "nvidia-kernel-source-${DRIVER_VERSION}-open" 2>/dev/null || true
fi

echo "  Driver package:  nvidia-driver-${DRIVER_VERSION} ${INSTALLED_DRIVER}"
echo "  Kernel source:   nvidia-kernel-source-${DRIVER_VERSION} ${INSTALLED_KSRC} (proprietary)"
if [ -n "$KERNEL_MODS" ]; then
    echo "  Kernel modules:  $KERNEL_MODS"
else
    echo "  WARNING: Proprietary .ko not found for ${RUNNING_KERNEL} — check after reboot"
fi

# ---- Step 7: Hold packages and set grub default ----
echo "[7/7] Holding packages and configuring boot..."

# Hold all installed nvidia packages
dpkg -l | grep -E "^ii.*nvidia" | awk '{print $2}' | xargs -r apt-mark hold
dpkg -l | grep -E "^ii.*linux-modules-nvidia" | awk '{print $2}' | xargs -r apt-mark hold

# Hold current kernel
dpkg -l | grep -E "^ii.*(linux-image|linux-headers)-${RUNNING_KERNEL}" | awk '{print $2}' | xargs -r apt-mark hold
dpkg -l | grep -E '^ii.*(linux-generic|linux-headers-generic|linux-image-generic)' | awk '{print $2}' | xargs -r apt-mark hold

# Set grub to boot the current kernel by default
ROOT_UUID=$(findmnt -no UUID /)
if [ -n "$ROOT_UUID" ]; then
    GRUB_ENTRY="gnulinux-advanced-${ROOT_UUID}>gnulinux-${RUNNING_KERNEL}-advanced-${ROOT_UUID}"
    sed -i "s|^GRUB_DEFAULT=.*|GRUB_DEFAULT=\"${GRUB_ENTRY}\"|" /etc/default/grub
    update-grub 2>&1 | grep -v "^Found\|^Warning\|^Adding\|^Generating"
    echo "  Grub default set to: ${RUNNING_KERNEL}"
fi

# Enable persistence mode
nvidia-smi -pm 1 2>/dev/null || true

echo ""
echo "=== Installation complete ==="
echo "Driver: nvidia-driver-${DRIVER_VERSION} ${INSTALLED_DRIVER} (proprietary/closed-source)"
echo ""
echo "REBOOT REQUIRED to load the new kernel module."
echo "After reboot, verify with: nvidia-smi && cat /proc/driver/nvidia/version"
