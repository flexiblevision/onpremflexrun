# Shared helpers for the flex-run deploy scripts.
#
# Sourced (not executed) by upgrades/start_servers.sh and
# system_server/system_server.sh, which both configure the same host services
# and used to carry their own drifting copies of this logic.
#
# POSIX sh only - these scripts are invoked as `sh <script>`, which is dash on
# Ubuntu.

# set_conf_directive <file> <key> <value>
#
# Ensures <file> contains exactly one "<key> <value>" line. Any existing
# definitions of <key> are removed first, so repeated runs converge on a single
# line instead of appending a new one every time - and a file already carrying
# duplicates from the old append-only behaviour is collapsed back to one.
#
# Commented-out defaults ("# maxmemory <bytes>") are left alone: the pattern
# requires the key at the start of the line. A key that is a prefix of another
# ("maxmemory" vs "maxmemory-policy") is not matched, because the pattern
# requires whitespace directly after the key.
set_conf_directive() {
    local file="$1"
    local key="$2"
    local value="$3"
    local tmp

    if [ ! -f "$file" ]; then
        echo "WARNING: $file does not exist - not setting $key"
        return 1
    fi

    tmp="$file.flexrun.$$"

    if ! sed "/^[[:space:]]*${key}[[:space:]]/d" "$file" >"$tmp"; then
        echo "ERROR: could not rewrite $file - leaving it unchanged"
        rm -f "$tmp"
        return 1
    fi

    printf '%s %s\n' "$key" "$value" >>"$tmp"

    # Keep the original owner and mode; redis.conf is a dpkg conffile owned by
    # redis:redis, and a root-owned replacement stops the service starting.
    chown --reference="$file" "$tmp" 2>/dev/null || true
    chmod --reference="$file" "$tmp" 2>/dev/null || true

    # Same directory, so this is a rename: the file is never partially written.
    if ! mv -f "$tmp" "$file"; then
        echo "ERROR: could not install new $file"
        rm -f "$tmp"
        return 1
    fi

    echo "set $key $value in $file"
}

# ---- container swap, with a way back ---------------------------------------
#
# The upgrade path removes a container and then creates the new one, so a
# crash-looping image leaves a dead service and the script carries on to the
# next container. Nothing restores anything.
#
# These four functions make the swap reversible: the previous container is
# renamed out of the way rather than deleted, so it is intact for the whole
# window and a rollback is a rename instead of a re-pull over a factory network.

# retire_container <name>
# Moves the running container aside. Returns 1 when it cannot be moved safely,
# and the caller must then leave it alone rather than proceed.
retire_container() {
    local name="$1"
    local prev="${name}_prev"

    # A leftover _prev means an earlier run died mid-swap. Discard it, or the
    # rename below fails and the only way forward would be deleting the live
    # container - exactly what this exists to avoid.
    if docker ps -a --format '{{.Names}}' | grep -q "^${prev}$"; then
        echo "discarding a leftover $prev from an interrupted run"
        docker rm -f "$prev" >/dev/null 2>&1
    fi

    if ! docker ps -a --format '{{.Names}}' | grep -q "^${name}$"; then
        echo "$name does not exist yet - nothing to retire"
        return 0
    fi

    docker stop "$name" >/dev/null 2>&1

    if ! docker rename "$name" "$prev"; then
        echo "ERROR: could not rename $name to $prev - leaving it in place"
        return 1
    fi

    echo "retired $name to $prev"
}

# rollback_container <name>
# The edge that does not exist in the current upgrade path.
rollback_container() {
    local name="$1"
    local prev="${name}_prev"

    echo "ROLLBACK: $name did not come up - restoring the previous image"
    docker rm -f "$name" >/dev/null 2>&1

    if ! docker ps -a --format '{{.Names}}' | grep -q "^${prev}$"; then
        echo "ERROR: there is no $prev to restore - $name is DOWN"
        return 1
    fi

    if ! docker rename "$prev" "$name"; then
        echo "ERROR: could not rename $prev back to $name - $name is DOWN"
        return 1
    fi

    if ! docker start "$name" >/dev/null 2>&1; then
        echo "ERROR: $name would not start from the previous image - $name is DOWN"
        return 1
    fi

    echo "rolled back: $name is running the previous image again"
}

# discard_previous <name>
# Only once the new container has proven itself.
discard_previous() {
    local prev="${1}_prev"
    if docker ps -a --format '{{.Names}}' | grep -q "^${prev}$"; then
        docker rm -f "$prev" >/dev/null 2>&1
        echo "discarded $prev"
    fi
}

# The upgrade plan: one line per component, "<component> <version> <ref>".
#
# Replaces reading versions out of $1..$8. Positional slots meant a fixed set of
# seven components in a fixed order, with the arch wedged into the middle by the
# dispatcher - so a new foundational service could not be expressed at all, and
# vernemq ended up upgraded by a hardcoded block outside the scheme. A named
# plan makes adding a component a data change, and lets each arch carry a
# different set without renumbering anything.
#
# Both readers fall back to the caller's positional value when there is no plan.
# That is load-bearing during a rollout: upgrade_flex_run.sh replaces this tree
# and the OLD runner then invokes the NEW scripts with positional arguments, so
# the first upgrade after this change has no plan file and must still work.

# plan_version <component> <fallback>
# The version to move this component to, or True to leave it alone.
plan_version() {
    local component="$1"
    local fallback="$2"

    if [ -z "${FLEXRUN_PLAN:-}" ] || [ ! -r "${FLEXRUN_PLAN:-}" ]; then
        echo "$fallback"
        return 0
    fi

    local found
    found="$(awk -v c="$component" '$1 == c { print $2; exit }' \
             "$FLEXRUN_PLAN" 2>/dev/null)"
    if [ -n "$found" ]; then echo "$found"; else echo "True"; fi
}

# plan_ref <component> <fallback_ref>
# What to pull and run: the digest a signed release pinned, or the caller's tag.
#
# Pulling by tag is why digest pinning was decorative on the device: a manifest
# recorded sha256:... and the device then fetched whatever the tag pointed at,
# which is what a repointed tag exploits. Only a *@sha256:* value is accepted,
# so a malformed plan cannot redirect a pull somewhere else.
plan_ref() {
    local component="$1"
    local fallback="$2"

    if [ -z "${FLEXRUN_PLAN:-}" ] || [ ! -r "${FLEXRUN_PLAN:-}" ]; then
        echo "$fallback"
        return 0
    fi

    local pinned
    pinned="$(awk -v c="$component" '$1 == c { print $3; exit }' \
              "$FLEXRUN_PLAN" 2>/dev/null)"

    case "$pinned" in
        *@sha256:*) echo "$pinned" ;;
        *)          echo "$fallback" ;;
    esac
}

# report_component <component> <outcome> <from> <to> <ref>
# Record what actually happened to one container. The step log says
# "updating X" then "X updated" regardless of outcome, so a rollback and a
# clean upgrade are indistinguishable in it - this is what tells them apart.
report_component() {
    [ -n "${FLEXRUN_RUN_ID:-}" ] || return 0
    [ -n "${FLEXRUN_RECORDER:-}" ] || return 0
    python3 "$FLEXRUN_RECORDER" -i "$FLEXRUN_RUN_ID" \
        --component "$1" --outcome "$2" \
        --from "${3:-}" --to "${4:-}" --ref "${5:-}" >/dev/null 2>&1 || true
}

# safe_pull <image>
# Pull, reporting the image that failed. Both the install and the upgrade path
# pull, so this lives here rather than in either one.
safe_pull() {
    local image="$1"
    echo "Pulling image: $image"
    if docker pull "$image"; then
        echo "Pull succeeded: $image"
        return 0
    fi
    echo "ERROR: Pull failed for $image"
    return 1
}

# smoke_http <name> <url> [attempts] [delay]
# A readiness check, not a liveness one. `docker ps` is satisfied by a container
# that started and cannot reach Mongo, which is the failure that presents as
# "the upgrade succeeded and the line stopped producing".
smoke_http() {
    local name="$1"
    local url="$2"
    local attempts="${3:-15}"
    local delay="${4:-2}"
    local i=0

    while [ "$i" -lt "$attempts" ]; do
        if curl -fsS --max-time 5 "$url" >/dev/null 2>&1; then
            echo "smoke: $name answered $url"
            return 0
        fi
        i=$((i + 1))
        sleep "$delay"
    done

    echo "ERROR: smoke: $name never answered $url ($attempts attempts)"
    return 1
}

# smoke_running <name> [attempts] [delay]
# For a container with no HTTP surface. Named honestly: this only proves the
# process is up, so it is a weaker check than smoke_http, not an equal one.
smoke_running() {
    local name="$1"
    local attempts="${2:-3}"
    local delay="${3:-3}"
    local i=0

    while [ "$i" -lt "$attempts" ]; do
        if docker ps --format '{{.Names}}' | grep -q "^${name}$"; then
            echo "smoke: $name is running (process only)"
            return 0
        fi
        i=$((i + 1))
        sleep "$delay"
    done

    echo "ERROR: smoke: $name is not running after $attempts checks"
    return 1
}

# smoke_settled <name> [settle_seconds]
# smoke_running returns on the first successful docker ps, so a container that
# starts and then crash-loops passes it. This re-checks after a settle period
# and fails on any restart. Use where there is no HTTP surface to probe.
smoke_settled() {
    local name="$1"
    local settle="${2:-8}"
    local restarts

    smoke_running "$name" 5 2 || return 1
    sleep "$settle"

    if ! docker ps --format '{{.Names}}' | grep -q "^${name}$"; then
        echo "ERROR: smoke: $name started then stopped within ${settle}s"
        return 1
    fi

    restarts="$(docker inspect -f '{{.RestartCount}}' "$name" 2>/dev/null || echo 0)"
    case "$restarts" in
        ''|*[!0-9]*) restarts=0 ;;
    esac
    if [ "$restarts" -gt 0 ]; then
        echo "ERROR: smoke: $name restarted $restarts time(s) - crash looping"
        return 1
    fi

    echo "smoke: $name settled (running, no restarts)"
    return 0
}

# ---- root crontab ----------------------------------------------------------
# Installed as one atomic `crontab -` call, so the crontab is never left
# half-written if the calling script dies mid-run. Entries between the markers
# are owned by flex-run and replaced on every run; anything a site has added
# outside them is preserved. The previous crontab is backed up first.
#
# Both the setup path (system_server/system_server.sh) and the upgrade path
# (upgrades/start_servers.sh) call install_crontab, so the two can no longer
# drift apart.

CRON_BEGIN='# BEGIN flex-run managed - replaced on upgrade, do not edit'
CRON_END='# END flex-run managed'

cron_managed_block() {
    cat <<EOF
$CRON_BEGIN
@reboot sudo sh $HOME/flex-run/scripts/fv_system_server_start.sh
@reboot sudo sh $HOME/flex-run/scripts/redis_server_start.sh
@reboot sudo sh $HOME/flex-run/scripts/tcp_server_start.sh
@reboot sudo sh $HOME/flex-run/scripts/gpio_server_start.sh
@reboot sleep 30 && sudo sh $HOME/flex-run/scripts/worker_server_start.sh
@reboot sudo sh $HOME/flex-run/scripts/sync_worker_start.sh
@reboot sudo sh $HOME/flex-run/scripts/filesystem_server.sh
@reboot sudo sh $HOME/flex-run/scripts/mediasystem_server.sh
@reboot sleep 30 && sudo sh $HOME/flex-run/scripts/hotspot.sh
@reboot sudo sh $HOME/flex-run/scripts/allocate_usbfs_memory.sh
@reboot sleep 50 && sudo sh $HOME/flex-run/scripts/restart_localprediction.sh
@reboot sudo sh $HOME/flex-run/scripts/start_job_watcher.sh
@reboot rm -rf ~/.cache/google-chrome
0 */8 * * * docker exec vision rm -rf /tmp
0 0 * * * forever restart $HOME/flex-run/system_server/worker_scripts/sync_worker.py
0 2 * * 0 sudo sh $HOME/flex-run/scripts/backup_node_flows.sh
@monthly sudo sh $HOME/flex-run/scripts/system_cleanup.sh
EOF

    # Hardware- and config-dependent entries.
    if nvidia-smi --query-gpu=name --format=csv 2>/dev/null | grep -q 'A4000'; then
        echo "@reboot sleep 50 && nvidia-smi --lock-gpu-clocks=1500,1500"
    fi
    if [ -e /etc/vsftpd.conf ]; then
        echo "@reboot sudo sh $HOME/flex-run/scripts/start_ftp_server.sh"
    fi

    echo "$CRON_END"
}

install_crontab() {
    local existing
    local managed
    local outside
    local staged
    local backup_dir
    local backup

    existing=$(mktemp)
    managed=$(mktemp)
    outside=$(mktemp)
    staged=$(mktemp)
    backup_dir=/var/backups/flex-run
    backup="$backup_dir/crontab.root.$(date +%Y%m%dT%H%M%S)"

    # No crontab yet is the normal case on a fresh unit, not an error.
    # SC2024: the redirect runs as the caller, which is correct here - the
    # target is our own mktemp file, not a privileged path.
    # shellcheck disable=SC2024
    sudo crontab -l >"$existing" 2>/dev/null || true

    mkdir -p "$backup_dir"
    cp "$existing" "$backup"

    cron_managed_block >"$managed"

    # Everything outside our markers belongs to the site and is kept.
    awk -v b="$CRON_BEGIN" -v e="$CRON_END" '
        $0 == b { skip = 1; next }
        $0 == e { skip = 0; next }
        !skip
    ' "$existing" >"$outside"

    # ...except a site line that duplicates a managed entry. Other scripts
    # (setup/ftp_server_setup.sh, system_server/timemachine/local_zip_push.sh)
    # still append some of these directly; without this they would accumulate
    # outside the markers and the job would run twice at boot.
    awk '
        function norm(s) {
            gsub(/[ \t]+/, " ", s); sub(/^ /, "", s); sub(/ $/, "", s); return s
        }
        NR == FNR { if ($0 !~ /^#/) m[norm($0)] = 1; next }
        { if (!(norm($0) in m)) print }
    ' "$managed" "$outside" >"$staged"

    cat "$managed" >>"$staged"

    # One replace. On failure the live crontab is untouched.
    # SC2024: reading our own mktemp file as the caller is intended; crontab
    # needs the privilege, the read does not.
    # shellcheck disable=SC2024
    if sudo crontab - <"$staged"; then
        echo "crontab installed; previous saved to $backup"
    else
        echo "ERROR: crontab install failed - live crontab unchanged, backup at $backup" >&2
        rm -f "$existing" "$managed" "$outside" "$staged"
        return 1
    fi

    rm -f "$existing" "$managed" "$outside" "$staged"
}

# ---- fstab -----------------------------------------------------------------
# ensure_fstab_entry <fstab-line>
#
# Appends the line only if an entry for the same mount source is not already
# present. /etc/fstab is not a key/value file, so this matches on the first
# field: a duplicate swap entry is what an unguarded append produces, and a
# duplicated swapfile line makes the mount fail at boot.
ensure_fstab_entry() {
    local line="$1"
    local source_field
    local fstab=/etc/fstab

    source_field=$(printf '%s' "$line" | awk '{print $1}')

    if [ -z "$source_field" ]; then
        echo "ERROR: refusing to add a malformed fstab entry: $line"
        return 1
    fi

    if awk -v s="$source_field" '$1 == s { found = 1 } END { exit !found }' "$fstab"; then
        echo "fstab already has an entry for $source_field - leaving it alone"
        return 0
    fi

    # `sudo cmd >> file` applies the redirect as the *calling* user, so the
    # append must go through tee. installSwapfile.sh runs unprivileged and
    # sudoes each step, so writing directly here fails on /etc/fstab.
    if ! printf '%s\n' "$line" | sudo tee -a "$fstab" >/dev/null; then
        echo "ERROR: could not append to $fstab"
        return 1
    fi
    echo "added to fstab: $line"
}

# Redis tuning. Override by exporting these before sourcing this file.
FLEXRUN_REDIS_CONF="${FLEXRUN_REDIS_CONF:-/etc/redis/redis.conf}"
FLEXRUN_REDIS_MAXMEMORY="${FLEXRUN_REDIS_MAXMEMORY:-10000000000}"
FLEXRUN_REDIS_MAXMEMORY_POLICY="${FLEXRUN_REDIS_MAXMEMORY_POLICY:-allkeys-lru}"

# configure_redis
#
# Applies the flex-run redis settings and restarts the service. flex-run owns
# these two directives: a value hand-tuned on the device is replaced, not
# appended below. That is deliberate - under the old behaviour a local override
# survived or was reverted depending on line order, which is worse than a
# consistent rule.
configure_redis() {
    set_conf_directive "$FLEXRUN_REDIS_CONF" maxmemory \
        "$FLEXRUN_REDIS_MAXMEMORY" || return 1
    set_conf_directive "$FLEXRUN_REDIS_CONF" maxmemory-policy \
        "$FLEXRUN_REDIS_MAXMEMORY_POLICY" || return 1

    systemctl restart redis.service
}
