#!/bin/sh
# Dispatch a container upgrade for this machine's architecture.
#
# Every argument is quoted. Unquoted, an empty version string is dropped by word
# splitting instead of being passed as an empty argument, so every later
# argument shifts left by one: the arch position receives a version number and
# every image name becomes fvonprem/<version>-<name>, which cannot be pulled.
# safe_pull then skips all of them and the upgrade reports success having done
# nothing. An empty version is reachable whenever the version service returns a
# 200 with an empty body.

set -eu

ARCH=$(arch)
UPGRADES="$HOME/flex-run/upgrades"

sh "$UPGRADES/install_dependencies.sh"

case "$ARCH" in
    aarch64) SYSTEM_ARCH=arm ;;
    x86_64)  SYSTEM_ARCH=x86 ;;
    *)
        echo "ERROR: unsupported architecture '$ARCH' - not upgrading" >&2
        exit 1
        ;;
esac

chmod +x "$UPGRADES/system_container_upgrades.sh"
sh "$UPGRADES/system_container_upgrades.sh" \
    "$1" "$2" "$3" "$SYSTEM_ARCH" "$4" "$5" "$6" "$7"
