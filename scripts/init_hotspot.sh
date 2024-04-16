#!/bin/bash

for dev in `ls /sys/class/net`; do
    if [[ "$dev" =~ ^wl.* ]]; then
        SSID="$(jq -r '.ssid' ./fvconfig.json)"
        sudo create_ap -n "$dev" $SSID password
        break
    fi
done