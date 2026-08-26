# Unattended script — never block on an interactive debconf prompt.
export DEBIAN_FRONTEND=noninteractive

apt install -y vsftpd
apt-get -y install isc-dhcp-server
apt-get -y install jq
apt-get -y --only-upgrade install google-chrome-stable
# linux-crashdump pulls in kexec-tools, which fires an interactive debconf prompt
# ("Should kexec-tools handle reboots?") that hangs the script even with -y.
# Preseed it to "no" (bootloader handles normal reboots; kdump uses kexec only on
# panic) and force the noninteractive frontend so apt never blocks on input.
echo "kexec-tools kexec-tools/load_kexec boolean false" | debconf-set-selections
DEBIAN_FRONTEND=noninteractive apt install -y linux-crashdump kdump-tools 2>/dev/null || echo "Warning: kdump not installed (no apt access) — kernel will still panic+reboot on lockups but won't capture crash dumps"
usermod -aG dialout visioncell

sudo rm /etc/xdg/autostart/update-notifier.desktop

dpkg -l | grep -E '^ii.*nvidia' | awk '{print $2}' | xargs -r apt-mark hold
dpkg -l | grep -E "^ii.*(linux-image|linux-headers)-$(uname -r)" | awk '{print $2}' | xargs -r apt-mark hold
dpkg -l | grep -E '^ii.*(linux-generic|linux-headers-generic|linux-image-generic)' | awk '{print $2}' | xargs -r apt-mark hold

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