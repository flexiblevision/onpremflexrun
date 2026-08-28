#!/bin/sh
# Refresh the flex-run orchestration tree from git.
#
# This script replaces the scripts that run immediately after it, so a partial
# or wrong result here is worse than no result at all: the caller would go on to
# run the OLD scripts against the NEW container versions. Every step is
# therefore checked, and any failure exits non-zero with the live tree
# untouched.
#
# --commit <sha> (or FLEXRUN_PIN_COMMIT) pins the tree to the commit a signed
# release names. Without it we take branch tip, so whoever can move the branch
# replaces the code that checks the signature. Unpinned stays the default
# because first install has no manifest to read a commit from; it warns.
#
# Exit codes:  0 updated   10 bad config   11 clone failed
#             12 clone tree invalid   13 not enough disk   14 copy failed
#             15 pinned commit not checked out

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

# --- resolve the pin --------------------------------------------------------
PIN_COMMIT="${FLEXRUN_PIN_COMMIT:-}"

while [ $# -gt 0 ]; do
    case "$1" in
        --commit)   PIN_COMMIT="${2:-}"; shift 2 ;;
        --commit=*) PIN_COMMIT="${1#--commit=}"; shift ;;
        *)          fail "unknown argument '$1'"; exit 10 ;;
    esac
done

# Full sha only: this reaches git, and a short sha or ref name would make "the
# commit the release names" ambiguous.
if [ -n "$PIN_COMMIT" ]; then
    case "$PIN_COMMIT" in
        *[!0-9a-f]*)
            fail "pinned commit '$PIN_COMMIT' is not lowercase hex"
            exit 10
            ;;
    esac
    if [ "${#PIN_COMMIT}" -ne 40 ]; then
        fail "pinned commit '$PIN_COMMIT' is not a full 40-character sha"
        exit 10
    fi
fi

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

# Shallow sha fetch is cheaper but not all remotes serve it; fall back to a full
# clone rather than dropping back to branch tip.
clone_pinned() {
    mkdir -p "$TMP_TREE" || return 1
    git -C "$TMP_TREE" init --quiet || return 1
    git -C "$TMP_TREE" remote add origin "$REPO_URL" || return 1

    if git -C "$TMP_TREE" fetch --quiet --depth 1 origin "$PIN_COMMIT" 2>/dev/null; then
        git -C "$TMP_TREE" checkout --quiet FETCH_HEAD || return 1
        return 0
    fi

    log "remote will not serve $PIN_COMMIT directly - cloning branch instead"
    rm -rf "$TMP_TREE"
    git clone --quiet --single-branch --branch "$BRANCH" "$REPO_URL" "$TMP_TREE" \
        || return 1
    git -C "$TMP_TREE" checkout --quiet "$PIN_COMMIT" || return 1
}

if [ -n "$PIN_COMMIT" ]; then
    log "fetching pinned commit $PIN_COMMIT"
    if ! clone_pinned; then
        fail "could not check out pinned commit $PIN_COMMIT on branch '$BRANCH' - live tree left as it was"
        exit 11
    fi
else
    log "cloning branch '$BRANCH'"
    log "WARNING: no commit pinned - taking branch tip, which is not the code any release was signed against"
    if ! git clone --quiet --depth 1 --single-branch --branch "$BRANCH" \
            "$REPO_URL" "$TMP_TREE"; then
        fail "clone of branch '$BRANCH' failed - live tree left as it was"
        exit 11
    fi
fi

# --- verify what we cloned --------------------------------------------------
for sentinel in $SENTINELS; do
    if [ ! -f "$TMP_TREE/$sentinel" ]; then
        fail "cloned tree is missing $sentinel - not copying it over the live tree"
        exit 12
    fi
done

COMMIT="$(git -C "$TMP_TREE" rev-parse HEAD 2>/dev/null || echo unknown)"

# Catches a checkout that quietly landed elsewhere.
if [ -n "$PIN_COMMIT" ] && [ "$COMMIT" != "$PIN_COMMIT" ]; then
    fail "release pins $PIN_COMMIT but the tree is at $COMMIT - not copying it over the live tree"
    exit 15
fi

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

# pinned= matters during an incident: "which code is this?" and "did a release
# vouch for it?" are different questions.
printf 'commit=%s\nbranch=%s\npinned=%s\nupdated=%s\n' \
    "$COMMIT" "$BRANCH" "$([ -n "$PIN_COMMIT" ] && echo yes || echo no)" \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    > "$LIVE_TREE/.flexrun_version" 2>/dev/null || true

if [ -n "$PIN_COMMIT" ]; then
    log "updated to pinned commit $COMMIT on branch '$BRANCH'"
else
    log "updated to $COMMIT (branch tip of '$BRANCH', unpinned)"
fi

# Let filesystem writes settle before the caller executes the scripts we just
# replaced.
sleep 3
