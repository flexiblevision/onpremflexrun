#!/bin/sh
# First install of a USB release on a device whose software predates USB
# releases. Run once, at the device, from the mounted stick:
#
#     sudo sh /media/<user>/<stick>/flexrun-releases/<release>/install.sh
#
# It puts this release's flex-run on the device from the stick, then hands over
# to its runner, which checks and installs everything else exactly as the
# settings screen does. After this, releases on a stick install from
# Settings > System.
#
# The signing keys arrive with this flex-run, so this first install is trusted
# because someone with physical access ran it. Every later stick is checked
# against the keys it installs.

set -eu

if [ "$(id -u)" != 0 ]; then
    echo "Run this with sudo." >&2
    exit 1
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
# flex-run lives under root's home, whoever ran sudo.
export HOME=/root

COMMIT="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["flexrun_commit"])' "$HERE/bundle.json")"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Installing flex-run ${COMMIT} from the USB stick..."
git clone --quiet "$HERE/flexrun.bundle" "$TMP/flex-run"
git -C "$TMP/flex-run" checkout --quiet "$COMMIT"
FLEXRUN_SOURCE="$HERE/flexrun.bundle" FLEXRUN_PIN_COMMIT="$COMMIT" \
    sh "$TMP/flex-run/upgrades/upgrade_flex_run.sh"

RUN_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
mkdir -p /var/log/flex-run
echo "Installing the release - leave the USB stick in until this finishes."
# The runner's own exit status, not tee's - plain sh has no pipefail.
{ python3 "$HOME/flex-run/system_server/upgrade_runner.py" --usb "$RUN_ID" "$HERE"; echo $? > "$TMP/status"; } 2>&1 \
    | tee -a "/var/log/flex-run/upgrade-$RUN_ID.log"
status="$(cat "$TMP/status")"
if [ "$status" = 0 ]; then
    echo "Installed. You can remove the USB stick."
else
    echo "The install did not complete (exit $status) - see /var/log/flex-run/upgrade-$RUN_ID.log" >&2
fi
exit "$status"
