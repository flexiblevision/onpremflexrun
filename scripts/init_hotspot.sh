#!/bin/bash

python3 $HOME/flex-run/scripts/name_generator.py
for dev in `ls /sys/class/net`; do
    if [[ "$dev" =~ ^wl.* ]]; then
        SSID="$(cat $HOME/flex-run/setup_constants/visioncell_ssid.txt)"
        sudo create_ap -n "$dev" $SSID password
        break
    fi
done