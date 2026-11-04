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
    sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
    echo
    echo "Run with no arguments for a guided walkthrough."
    echo "KEY=<gcpkms://...>   sign with a different key"
    exit "${1:-0}"
}

# --- the wizard -------------------------------------------------------------
# Every step of a release is a decision, and the flags that express them are
# only discoverable if you already know them. Bare invocation asks instead.
# Flags still work unchanged, so scripts and CI are unaffected.

ask() {
    # ask <prompt> <default>; answer on stdout, prompts on stderr so the
    # caller can capture the value.
    printf '%s [%s]: ' "$1" "$2" >&2
    read -r _reply
    [ -n "$_reply" ] && printf '%s' "$_reply" || printf '%s' "$2"
}

choose() {
    # choose <prompt> <value:label> ...  -> chosen value on stdout
    _prompt="$1"; shift
    printf '\n%s\n' "$_prompt" >&2
    _i=0
    for _opt in "$@"; do
        _i=$((_i + 1))
        printf '  %d) %s\n' "$_i" "${_opt#*:}" >&2
    done
    while :; do
        printf 'choice [1]: ' >&2
        read -r _n
        [ -z "$_n" ] && _n=1
        _i=0
        for _opt in "$@"; do
            _i=$((_i + 1))
            if [ "$_i" = "$_n" ]; then
                printf '%s' "${_opt%%:*}"
                return 0
            fi
        done
        printf 'not a choice\n' >&2
    done
}

wizard() {
    # A wizard that hangs is worse than one that never runs: without a
    # terminal there is nobody to answer, so say what to type instead.
    if [ ! -t 0 ]; then
        echo "no terminal - pass a command instead of running the wizard" >&2
        usage 2
    fi

    echo "flexrun release" >&2
    action="$(choose 'What do you want to do?' \
        'cut:Cut a new release' \
        'candidates:See what CI has published, and update components.json' \
        'check:Check everything is set up (writes nothing)' \
        'promote:Promote a release that is already signed' \
        'rollback:Point a channel back at an earlier release')"

    case "$action" in
        check|candidates)
            set -- "$action"
            ;;
        promote|rollback)
            channel="$(choose 'Which channel?' 'stable:stable' 'beta:beta')"
            counter="$(ask 'Which counter? (blank = the release just cut)' '')"
            set -- promote --channel "$channel"
            [ -n "$counter" ] && set -- "$@" --counter "$counter"
            ;;
        cut)
            arch="$(choose 'Which architecture?' 'x86:x86' 'arm:arm')"
            source="$(choose 'Which component versions?' \
                'file:release/components.json - what you have chosen to ship' \
                'survey:Ask the registry what CI has published, and update that file first' \
                'stable:the live latest_stable_version endpoint - what the fleet runs now')"

            # The survey was its own verb, which meant you only ran it if you
            # already knew it existed - so components.json went stale and
            # releases pinned whatever was last edited by hand. Asking here is
            # the same operation at the moment it is relevant.
            if [ "$source" = survey ]; then
                echo >&2
                if FLEXRUN_WIZARD=1 "$0" candidates; then
                    if git diff --quiet -- release/components.json; then
                        echo >&2
                        echo "  nothing to change - carrying on with the file as it stands" >&2
                        source='file'
                    else
                        # The commit IS the decision to ship these versions, and
                        # the only record of who made it - so it stays an
                        # explicit answer, with the moves above already on
                        # screen. But it is asked here rather than sent away to
                        # a second run: the cut cannot proceed over a dirty tree
                        # anyway (the manifest pins HEAD), so leaving now means
                        # answering every question again for nothing.
                        echo >&2
                        go="$(ask 'Commit release/components.json and carry on with the cut? (yes/no)' 'no')"
                        case "$go" in
                            y|yes|Y|YES)
                                msg="$(ask 'Commit message' 'promote component versions')"
                                git commit -q -m "$msg" -- release/components.json
                                echo "  committed." >&2
                                source='file'
                                ;;
                            *)
                                echo >&2
                                echo "  left uncommitted - nothing was cut." >&2
                                echo "  commit it yourself, then run ./release_cut.sh again." >&2
                                echo >&2
                                exit 0
                                ;;
                        esac
                    fi
                else
                    echo >&2
                    echo "  the survey failed - the registry may be unreachable, or" >&2
                    echo "  'docker login' may have expired." >&2
                    keep="$(ask 'Cut from release/components.json as it stands? (yes/no)' 'no')"
                    case "$keep" in
                        y|yes|Y|YES) source='file' ;;
                        *) echo 'nothing was done' >&2; exit 1 ;;
                    esac
                fi
            fi
            # Read the series we are on so the prompt can say what "blank"
            # means. "1.0" is the natural answer to a bare "number?", and it
            # used to sail past the wizard and fail in argparse at the very
            # end - after every other question had been answered.
            cur_major="$(python3 -c "
from release.build_release import parse_version_file
try:
    print(parse_version_file(open('release/VERSION').read())[0])
except Exception:
    print('')" 2>/dev/null || echo '')"

            while :; do
                if [ -n "$cur_major" ]; then
                    major="$(ask "Start a new major series? Whole number only, e.g. $((cur_major + 1)). Blank stays on ${cur_major}.x" '')"
                else
                    major="$(ask 'Start a new major series? Whole number only, e.g. 2. Blank to continue' '')"
                fi
                [ -z "$major" ] && break
                case "$major" in
                    *[!0-9]*)
                        echo "  '$major' is not a whole number - the major only, not a version like 1.0" >&2
                        continue ;;
                esac
                if [ -n "$cur_major" ] && [ "$major" -le "$cur_major" ]; then
                    echo "  already on series $cur_major - a major jump goes forwards" >&2
                    continue
                fi
                break
            done
            after="$(choose 'Promote it after signing?' \
                'none:No - sign only, decide later' \
                'beta:Yes, to beta - try it on one device first' \
                'stable:Yes, to stable - the whole fleet follows this')"

            set -- cut --arch "$arch"
            [ "$source" = stable ] && set -- "$@" --from-stable
            [ -n "$major" ] && set -- "$@" --major "$major"
            [ "$after" != none ] && set -- "$@" --channel "$after"
            ;;
    esac

    # Printed so the wizard teaches the flags rather than hiding them - the
    # next release can skip it.
    printf '\n  ./release_cut.sh %s\n\n' "$*" >&2
    confirm="$(ask 'Run this? (yes/no)' 'yes')"
    case "$confirm" in
        y|yes|Y|YES) ;;
        *) echo 'nothing was done' >&2; exit 1 ;;
    esac
    printf '\n' >&2

    # exec, not return: `set --` inside a function rebinds that function's
    # positional parameters, so returning would drop every choice just made
    # and leave the caller with no arguments at all. Re-invoking also makes the
    # line printed above literally the command that runs.
    exec "$0" "$@"
}

if [ $# -eq 0 ]; then
    wizard
fi

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
    -h|--help|help)
        usage 0
        ;;
    *)
        echo "unknown command '$verb'" >&2
        usage 2
        ;;
esac
