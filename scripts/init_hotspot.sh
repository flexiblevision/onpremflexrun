#!/bin/bash

for dev in `ls /sys/class/net`; do
    if [[ "$dev" =~ ^wlp.* ]]; then
        sudo create_ap -n "$dev" visioncell password
        break
    fi
done