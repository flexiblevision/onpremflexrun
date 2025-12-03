#!/bin/sh
# Generate unique serial number from MAC address

MAC=$(ip link show | awk '/ether/ {print $2; exit}' | tr -d ':' | tr '[:lower:]' '[:upper:]')

if [ -z "$MAC" ]; then
    echo "Error: Could not get MAC address" >&2
    exit 1
fi

echo "SN-${MAC}"