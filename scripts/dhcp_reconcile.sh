#!/bin/sh
# Brings dhcpd back for camera ports it should be serving but is not.
cd "$HOME/flex-run" || exit 1
python3 -m helpers.config_helper reconcile 2>&1 | logger -t fv-dhcp
