#!/bin/sh
# Write a published release onto a USB stick. See release/usb.py.
#
#   ./usb_release_writer.sh                        finds the stick, asks which release
#   ./usb_release_writer.sh --channel beta         what beta serves
#   ./usb_release_writer.sh --counter 42           a specific published release
#   ./usb_release_writer.sh --arch arm             for arm devices
#   ./usb_release_writer.sh /media/$USER/STICK     when more than one stick is mounted
#
# Nothing is cut or signed: the release must already be signed by
# ./release_cut.sh. The stick must be exFAT or ext4.
set -eu

cd "$(dirname "$0")"

case "${1:-}" in
    -h|--help|help)
        sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
        exit 0
        ;;
esac

exec python3 -m release.usb "$@"
