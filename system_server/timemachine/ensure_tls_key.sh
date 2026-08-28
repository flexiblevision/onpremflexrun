#!/bin/sh
# Give this device its own TLS key for the time-machine RTSP/HLS server.
#
# WHY THIS EXISTS
#
# fvonprem/x86-rtspserver:prod ships with server.key and server.crt baked into
# the image, so every device in the fleet served TLS with the same private key.
# That same key was also committed to this repository - which is public - in
# 2022, so it has been downloadable by anyone for years. Anyone holding it can
# impersonate a device's video server or decrypt RTSP/HLS traffic to it.
#
# Rotating the image alone would not fix the sharing: one key for the whole
# fleet means one compromise reaches every site. So the key is generated here,
# on the device, once, and mounted over the image's copy at run time.
#
# IDEMPOTENT ON PURPOSE. An upgrade must not hand a device a new identity: any
# client that pinned the old certificate would start failing after a routine
# update, which on a factory floor is a site visit. Regenerate deliberately with
# FORCE_NEW_TLS_KEY=1, not as a side effect of upgrading.
set -eu

DIR="${1:-$HOME/flex-run/system_server/timemachine}"
KEY="$DIR/server.key"
CRT="$DIR/server.crt"
DAYS=3650

if [ -n "${FORCE_NEW_TLS_KEY:-}" ]; then
    echo "FORCE_NEW_TLS_KEY set - replacing this device's TLS key"
    rm -f "$KEY" "$CRT"
fi

# Both must exist and agree. A key without its certificate, or a mismatched
# pair, makes rtsp-simple-server fail to open its encrypted listeners - and it
# fails at start, long after whoever broke it has moved on.
if [ -s "$KEY" ] && [ -s "$CRT" ]; then
    key_mod="$(openssl rsa -in "$KEY" -noout -modulus 2>/dev/null || true)"
    crt_mod="$(openssl x509 -in "$CRT" -noout -modulus 2>/dev/null || true)"
    if [ -n "$key_mod" ] && [ "$key_mod" = "$crt_mod" ]; then
        echo "TLS key already present for this device: $KEY"
        exit 0
    fi
    echo "existing key and certificate do not match - regenerating" >&2
    rm -f "$KEY" "$CRT"
fi

if ! command -v openssl >/dev/null 2>&1; then
    echo "ERROR: openssl not found - cannot generate a TLS key for this device." >&2
    echo "       Install openssl and re-run, or the RTSP server will fall back" >&2
    echo "       to the key published in the image, which is public." >&2
    exit 1
fi

mkdir -p "$DIR"

# CN is the hostname rather than a person: these are self-signed, so the only
# thing the subject can usefully say is which device it belongs to.
HOSTNAME_CN="$(hostname 2>/dev/null || echo flexrun-device)"

echo "generating a TLS key unique to this device..."
openssl req -x509 -newkey rsa:2048 -sha256 -nodes \
    -keyout "$KEY" -out "$CRT" -days "$DAYS" \
    -subj "/C=US/ST=CA/O=Flexible Vision/OU=OnPrem/CN=$HOSTNAME_CN" \
    >/dev/null 2>&1

# The private key must not be world-readable. It is bind-mounted into a
# container that runs as root, so 600 owned by the installing user is enough.
chmod 600 "$KEY"
chmod 644 "$CRT"

echo "wrote $KEY (this device only)"
openssl x509 -in "$CRT" -noout -subject -dates 2>/dev/null || true
