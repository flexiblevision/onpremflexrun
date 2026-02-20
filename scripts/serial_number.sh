#!/bin/sh
# Generate unique device serial number
# Priority: motherboard serial > product serial > CPU serial > MAC address
# Prefix: FV-S (ARM), FV-I (VC610), SN- (everything else)

# Determine prefix based on architecture and hostname
ARCH=$(uname -m)
HOSTNAME=$(hostname)

if [ "$HOSTNAME" = "visioncell-VC610" ]; then
    PREFIX="FV-I"
elif [ "$ARCH" = "aarch64" ]; then
    PREFIX="FV-S"
else
    PREFIX="SN"
fi

# Helper to check if serial is valid (not a placeholder)
is_valid_serial() {
    [ -n "$1" ] && [ "$1" != "NONE" ] && [ "$1" != "NOTSPECIFIED" ] && [ "$1" != "DEFAULTSTRING" ] && [ "$1" != "TOBEFILLEDBYOEM" ]
}

# Try motherboard serial (most reliable for x86 systems)
SERIAL=$(cat /sys/class/dmi/id/board_serial 2>/dev/null | tr -d ' ' | tr '[:lower:]' '[:upper:]')
if is_valid_serial "$SERIAL"; then
    echo "[board_serial]" >&2
    echo "${PREFIX}-${SERIAL}"
    exit 0
fi

# Try product serial
SERIAL=$(cat /sys/class/dmi/id/product_serial 2>/dev/null | tr -d ' ' | tr '[:lower:]' '[:upper:]')
if is_valid_serial "$SERIAL"; then
    echo "[product_serial]" >&2
    echo "${PREFIX}-${SERIAL}"
    exit 0
fi

# Try CPU serial (common on ARM/Raspberry Pi)
SERIAL=$(grep -i 'serial' /proc/cpuinfo 2>/dev/null | awk -F': ' '{print $2}' | tr -d ' ' | tr '[:lower:]' '[:upper:]')
if [ -n "$SERIAL" ] && [ "$SERIAL" != "0000000000000000" ]; then
    echo "[cpu_serial]" >&2
    echo "${PREFIX}-${SERIAL}"
    exit 0
fi

# Fallback to MAC address
MAC=$(cat /sys/class/net/eno1/address 2>/dev/null || cat /sys/class/net/eth0/address 2>/dev/null || ip link show | awk '/ether/ {print $2; exit}')
MAC=$(echo "$MAC" | tr -d ':' | tr '[:lower:]' '[:upper:]')
if [ -n "$MAC" ] && [ "$MAC" != "000000000000" ]; then
    echo "${PREFIX}-${MAC}"
    exit 0
fi

echo "Error: Could not determine device serial" >&2
exit 1