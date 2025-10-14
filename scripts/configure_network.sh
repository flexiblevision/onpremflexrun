#!/bin/bash

sysctl_lines=(
    "net.core.rmem_max=52428800"
    "net.core.rmem_default=52428800"
    "net.core.wmem_max=52428800"
    "net.core.wmem_default=52428800"
    "net.ipv4.tcp_rmem = 4096 1048576 52428800"
    "net.ipv4.tcp_wmem = 4096 1048576 52428800"
)

# Check if the script is run as root
if [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root. Please use sudo."
    exit 1
fi

echo "Checking and updating /etc/sysctl.conf..."
for line in "${sysctl_lines[@]}"; do
    # Escape special characters for grep if the line contains them (e.g., '#', '=')
    # For this specific set of lines, a simple grep should work, but for robustness:
    escaped_line=$(echo "$line" | sed 's/[][\/.^$*+?(){}|-]/\\&/g')

    if ! grep -q "^${escaped_line}$" /etc/sysctl.conf; then
        echo "Adding: $line"
        echo "$line" | tee -a /etc/sysctl.conf > /dev/null
    else
        echo "Already exists: $line"
    fi
done


sudo sysctl -p
echo ""
echo "Update complete."