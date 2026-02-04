#!/bin/sh
# Generate unique device serial number
# Priority: motherboard serial > product serial > CPU serial > MAC address

# Try motherboard serial (most reliable for x86 systems)
SERIAL=$(cat /sys/class/dmi/id/board_serial 2>/dev/null | tr -d ' ' | tr '[:lower:]' '[:upper:]')
if [ -n "$SERIAL" ] && [ "$SERIAL" != "NONE" ] && [ "$SERIAL" != "NOT SPECIFIED" ]; then
    echo "SN-${SERIAL}"
    exit 0
fi

# Try product serial
SERIAL=$(cat /sys/class/dmi/id/product_serial 2>/dev/null | tr -d ' ' | tr '[:lower:]' '[:upper:]')
if [ -n "$SERIAL" ] && [ "$SERIAL" != "NONE" ] && [ "$SERIAL" != "NOT SPECIFIED" ]; then
    echo "SN-${SERIAL}"
    exit 0
fi

# Try CPU serial (common on ARM/Raspberry Pi)
SERIAL=$(grep -i 'serial' /proc/cpuinfo 2>/dev/null | awk -F': ' '{print $2}' | tr -d ' ' | tr '[:lower:]' '[:upper:]')
if [ -n "$SERIAL" ] && [ "$SERIAL" != "0000000000000000" ]; then
    echo "SN-${SERIAL}"
    exit 0
fi

# Fallback to MAC address
MAC=$(cat /sys/class/net/eno1/address 2>/dev/null || cat /sys/class/net/eth0/address 2>/dev/null || ip link show | awk '/ether/ {print $2; exit}')
MAC=$(echo "$MAC" | tr -d ':' | tr '[:lower:]' '[:upper:]')
if [ -n "$MAC" ] && [ "$MAC" != "000000000000" ]; then
    echo "SN-${MAC}"
    exit 0
fi

echo "Error: Could not determine device serial" >&2
exit 1