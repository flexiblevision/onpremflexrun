#!/bin/sh
# First-run setup. Invoked by deploy.py from the repo root, so the relative
# paths below are intentional.
#
# Arguments are quoted for the same reason as in upgrade_system.sh: unquoted, an
# empty version string vanishes during word splitting and shifts the arch
# argument out of position, so every image name is built wrong.

ARCH=$(arch)

apt update -y
docker network create -d bridge imagerie_nw || true
usermod -aG dialout visioncell || true

case "$ARCH" in
    aarch64) SYSTEM_ARCH=arm ;;
    x86_64)  SYSTEM_ARCH=x86 ;;
    *)
        echo "ERROR: unsupported architecture '$ARCH' - cannot set up this machine" >&2
        exit 1
        ;;
esac

# system_setup.sh's exit code was discarded here, so this script always
# reported whatever system_server.sh returned - a failed install looked clean.
sh ./setup/system_setup.sh "$1" "$2" "$3" "$SYSTEM_ARCH" "$4" "$5" "$6" "$7"
SETUP_CODE=$?
if [ "$SETUP_CODE" -ne 0 ]; then
    echo "[local_setup] system_setup.sh failed (exit $SETUP_CODE) - not starting servers" >&2
    exit "$SETUP_CODE"
fi

chmod +x ./system_server/system_server.sh
sh ./system_server/system_server.sh
