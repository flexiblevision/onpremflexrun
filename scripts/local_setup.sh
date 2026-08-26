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

sh ./setup/system_setup.sh "$1" "$2" "$3" "$SYSTEM_ARCH" "$4" "$5" "$6" "$7"

chmod +x ./system_server/system_server.sh
sh ./system_server/system_server.sh
