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
