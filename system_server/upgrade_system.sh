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
RUNNER="$HOME/flex-run/system_server/upgrade_runner.py"

# No run id means the caller is a release from before upgrade_runner: its
# /upgrade chose the versions itself and calls this directly, so nothing would
# be verified, pinned to the release's commit, or recorded - the device would
# need a second upgrade to land on the release. Give the whole upgrade to the
# runner instead. Detached, because that caller runs inside server.py and the
# upgrade ends by restarting it.
if [ -z "${FLEXRUN_RUN_ID:-}" ] && [ -r "$RUNNER" ]; then
    LOG_DIR="${FLEXRUN_UPGRADE_LOG_DIR:-/var/log/flex-run}"
    mkdir -p "$LOG_DIR"
    run_id="$(python3 -c 'import uuid; print(uuid.uuid4())')"
    ( setsid python3 "$RUNNER" --release "$run_id" \
        </dev/null >>"$LOG_DIR/upgrade-$run_id.log" 2>&1 & )
    echo "upgrade handed to upgrade_runner as run $run_id - log: $LOG_DIR/upgrade-$run_id.log"
    exit 0
fi

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
