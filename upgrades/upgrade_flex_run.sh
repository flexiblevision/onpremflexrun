#!/bin/sh
# Refresh the flex-run orchestration tree from git.
#
# This script replaces the scripts that run immediately after it, so a partial
# or wrong result here is worse than no result at all: the caller would go on to
# run the OLD scripts against the NEW container versions. Every step is
# therefore checked, and any failure exits non-zero with the live tree
# untouched.
#
# Exit codes:  0 updated   10 bad config   11 clone failed
#             12 clone tree invalid   13 not enough disk   14 copy failed

set -eu

REPO_URL='https://github.com/flexiblevision/onpremflexrun.git'
CONFIG="$HOME/fvconfig.json"
LIVE_TREE="$HOME/flex-run"
TMP_TREE="$HOME/flex-run-temp"

# Files that must exist in a freshly cloned tree for it to be usable. If any is
# missing we are not looking at flex-run and must not copy over the live tree.
SENTINELS='deploy.py requirements.txt system_server/server.py system_server/upgrade_runner.py upgrades/system_container_upgrades.sh upgrades/lib/deploy_common.sh'

log()  { echo "[upgrade_flex_run] $*"; }
fail() { echo "[upgrade_flex_run] ERROR: $*" >&2; }

cleanup() { rm -rf "$TMP_TREE"; }
trap cleanup EXIT HUP INT TERM

# --- resolve the branch -----------------------------------------------------
# jq prints the string "null" and exits 0 when .branch is absent, so an
# unchecked value here becomes `git clone --branch null`.
if [ ! -r "$CONFIG" ]; then
    fail "$CONFIG is missing or unreadable - cannot determine which branch to deploy"
    exit 10
fi

BRANCH="$(jq -r '.branch' "$CONFIG" 2>/dev/null || echo '')"

case "$BRANCH" in
    ''|null)
        fail "no .branch in $CONFIG - refusing to guess"
        exit 10
        ;;
    *[!A-Za-z0-9._/-]*)
        fail "branch '$BRANCH' contains characters that are not valid in a git ref"
        exit 10
        ;;
esac

# --- clone ------------------------------------------------------------------
rm -rf "$TMP_TREE"

log "cloning branch '$BRANCH'"
if ! git clone --quiet --depth 1 --single-branch --branch "$BRANCH" \
        "$REPO_URL" "$TMP_TREE"; then
    fail "clone of branch '$BRANCH' failed - live tree left as it was"
    exit 11
fi

# --- verify what we cloned --------------------------------------------------
for sentinel in $SENTINELS; do
    if [ ! -f "$TMP_TREE/$sentinel" ]; then
        fail "cloned tree is missing $sentinel - not copying it over the live tree"
        exit 12
    fi
done

COMMIT="$(git -C "$TMP_TREE" rev-parse HEAD 2>/dev/null || echo unknown)"

# --- check there is room before starting the copy ---------------------------
# A copy that runs out of space part-way leaves a tree matching no commit.
NEED_KB="$(du -sk "$TMP_TREE" | awk '{print $1}')"
FREE_KB="$(df -Pk "$LIVE_TREE" | awk 'NR==2 {print $4}')"
if [ "$FREE_KB" -lt "$((NEED_KB * 2))" ]; then
    fail "need $((NEED_KB * 2))KB free to copy safely, have ${FREE_KB}KB - aborting"
    exit 13
fi

# --- copy into place --------------------------------------------------------
# Additive, matching previous behaviour: files removed upstream are NOT deleted
# from the device. Do not "fix" that here with rsync --delete - the live tree
# also holds untracked device state (setup/mqtt/ssl* certificates and private
# keys, vernemq-local.conf, creds.txt) that a clean sync would destroy.
# Pruning stale files needs an explicit keep-list; see R-07.
log "updating $LIVE_TREE from $COMMIT"
if ! cp -r "$TMP_TREE"/* "$LIVE_TREE"/; then
    fail "copy into $LIVE_TREE failed - tree may be inconsistent, do not continue"
    exit 14
fi

# Record what was deployed so the running version is answerable on the device.
printf 'commit=%s\nbranch=%s\nupdated=%s\n' \
    "$COMMIT" "$BRANCH" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    > "$LIVE_TREE/.flexrun_version" 2>/dev/null || true

log "updated to $COMMIT on branch '$BRANCH'"

# Let filesystem writes settle before the caller executes the scripts we just
# replaced.
sleep 3
