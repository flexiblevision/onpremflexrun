#!/bin/sh
# Cut a release. See release/RELEASING.md for the whole procedure.
#
#   ./release_cut.sh check              what is set up, writes nothing
#   ./release_cut.sh candidates         what CI has published, rewrites components.json
#   ./release_cut.sh cut                        the guided cut for x86
#   ./release_cut.sh cut --arch arm             the same for arm
#   ./release_cut.sh cut --channel beta         cut, then offer to put it on beta
#   ./release_cut.sh cut --channel stable       cut, then offer to ship it
#   ./release_cut.sh promote            ship the cut you just signed, and deploy
#   ./release_cut.sh promote --channel beta   ship it to one device first
#   ./release_cut.sh rollback --counter 3     put a channel back
#
# Anything after the verb is passed straight through to release.cut, so
# --allow-dirty, --arch, --strict-provenance and the rest still work.
set -eu

cd "$(dirname "$0")"

# The signing key. Public information - authority to sign lives in IAM, not in
# knowing this path. Override with KEY=... for the standby key or a rotation.
: "${KEY:=gcpkms://projects/flexible-vision-staging/locations/us-central1/keyRings/onprem-release-signing/cryptoKeys/release-signing/versions/1}"

# Reuses `docker login`. The fvonprem repos are private, so digest resolution
# is a 401 without credentials.
COMMON="--use-docker-login --key $KEY"

usage() {
    sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
    echo
    echo "KEY=<gcpkms://...>   sign with a different key"
    exit "${1:-0}"
}

verb="${1:-}"
[ $# -gt 0 ] && shift || true

case "$verb" in
    check)
        # shellcheck disable=SC2086
        exec python3 -m release.cut --preflight-only $COMMON "$@"
        ;;
    candidates)
        # Rewrites release/components.json from what CI has published, then
        # stops. Committing the diff is the promote - this does not do that.
        # shellcheck disable=SC2086
        exec python3 -m release.cut --update-components $COMMON "$@"
        ;;
    cut)
        # --public-key verifies the signature the way a device will, before
        # anything is published. Skipped silently if no trust store is set up,
        # rather than refusing to cut over it.
        TRUST="${TRUST:-$HOME/.flexrun-trust}"
        VERIFY=""
        [ -e "$TRUST" ] && VERIFY="--public-key $TRUST"

        # The manifest the channel currently points at - NOT the last cut.
        # A cut that was never promoted was never run by any device, so
        # diffing against it reports a transition nobody made: a baseline
        # release read as six downgrades from an unpromoted test release.
        PREV=""
        PREV_FILE="$(mktemp)"
        if python3 -c "
import sys
from release.promote import promoted_manifest
raw = promoted_manifest('${ARCH:-x86}')
sys.exit(0 if raw and open('$PREV_FILE','wb').write(raw) else 1)
" 2>/dev/null; then
            PREV="--previous $PREV_FILE"
        else
            rm -f "$PREV_FILE"
        fi

        # shellcheck disable=SC2086
        exec python3 -m release.cut $COMMON $VERIFY $PREV "$@"
        ;;
    promote)
        # Deliberately separate from `cut`. Signing says "this is a genuine
        # release"; promoting says "the fleet should run it". This does the
        # three steps that used to be manual - write the release, point the
        # channel, deploy - and then asks the proxy what it actually serves,
        # because a deploy that succeeds but is unreachable is the failure that
        # matters and gcloud does not report it.
        exec python3 -m release.promote "$@"
        ;;
    rollback)
        # Same machinery: point a channel at a release already published.
        exec python3 -m release.promote "$@"
        ;;
    ''|-h|--help|help)
        usage 0
        ;;
    *)
        echo "unknown command '$verb'" >&2
        usage 2
        ;;
esac
