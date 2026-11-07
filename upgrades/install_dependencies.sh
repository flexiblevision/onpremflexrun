# Unattended script — never block on an interactive debconf prompt.
export DEBIAN_FRONTEND=noninteractive

# A USB release has no network: apt has nothing to reach, and pip takes the
# stick's packages for this Python. PIP_* reach deploy.py's own pip as well.
if [ -n "${FLEXRUN_PACKAGES:-}" ]; then
    PYTAG="py$(python3 -c 'import sys; print("%d%d" % sys.version_info[:2])')"
    if [ ! -d "$FLEXRUN_PACKAGES/$PYTAG" ]; then
        echo "ERROR: this release carries no Python packages for $(python3 --version 2>&1)" >&2
        exit 1
    fi
    export PIP_NO_INDEX=1
    export PIP_FIND_LINKS="$FLEXRUN_PACKAGES/$PYTAG"
fi

if [ -z "${FLEXRUN_PACKAGES:-}" ]; then
apt install -y vsftpd
apt-get -y install isc-dhcp-server
apt-get -y install jq
# create_ap needs iw to create the AP virtual interface. Without it create_ap
# falls back to --no-virt, binds the physical wifi adapter and marks it
# unmanaged, which leaves the device with no wifi client.
apt-get -y install iw
apt-get -y --only-upgrade install google-chrome-stable
# linux-crashdump pulls in kexec-tools, which fires an interactive debconf prompt
# ("Should kexec-tools handle reboots?") that hangs the script even with -y.
# Preseed it to "no" (bootloader handles normal reboots; kdump uses kexec only on
# panic) and force the noninteractive frontend so apt never blocks on input.
echo "kexec-tools kexec-tools/load_kexec boolean false" | debconf-set-selections
DEBIAN_FRONTEND=noninteractive apt install -y linux-crashdump kdump-tools 2>/dev/null || echo "Warning: kdump not installed (no apt access) — kernel will still panic+reboot on lockups but won't capture crash dumps"
fi
usermod -aG dialout visioncell

sudo rm /etc/xdg/autostart/update-notifier.desktop

bash "$(dirname "$0")/../helpers/pin_kernel.sh"
bash "$(dirname "$0")/../helpers/install_r8127.sh"
bash "$(dirname "$0")/../helpers/install_touchscreen_pin.sh"

export PYTHONPATH="${PYTHONPATH}:${HOME}/flex-run"

python3 "$HOME/flex-run/setup/management.py"

# --break-system-packages only exists on pip >= 23.0 (PEP 668). Older pip both
# rejects the flag and doesn't need it, so only pass it when supported.
# Branch rather than interpolate: quoting an empty PIP_BSP would pass "" to pip
# as a requirement and fail, and leaving it unquoted relies on word splitting.
REQUIREMENTS="$HOME/flex-run/requirements.txt"
if pip3 install --help 2>/dev/null | grep -q -- --break-system-packages; then
    pip3 install --break-system-packages --ignore-installed -r "$REQUIREMENTS"
else
    pip3 install --ignore-installed -r "$REQUIREMENTS"
fi

# --ignore-installed skips the uninstall, so every version change leaves the old
# version's metadata behind, and a pin that moves backwards can leave a compiled
# module shadowing the .py replacing it. Either breaks imports only when the
# services start, so check and repair them here, where a failure stops the
# upgrade.
python3 "$HOME/flex-run/deploy.py" --repair-deps
