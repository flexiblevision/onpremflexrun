#!/bin/sh
# Install the release signing public keys into this device's trust store.
#
# Both keys go on every device, active and standby. A rotation is then "start
# signing with the standby" and needs no trust update - which matters because
# pushing new trust to a fleet that can only be reached through an update it
# does not yet trust is a bootstrap problem with no good answer.
#
# The keys are public and committed to this repository on purpose. Fetching
# them at install would mean trusting the fetch, which is the thing the
# signature exists to avoid.
#
# Idempotent: provision() refuses to overwrite a different key under the same
# name, so a re-run is a no-op and a tampered store is an error, not a silent
# swap.
set -eu

TRUST_DIR="${FLEXRUN_TRUST_DIR:-/etc/flexrun/keys}"
KEY_DIR="$(dirname "$0")/../release/keys"

if [ ! -d "$KEY_DIR" ]; then
    echo "ERROR: no keys at $KEY_DIR - the deploy tree is incomplete" >&2
    exit 1
fi

mkdir -p "$TRUST_DIR"

python3 - "$KEY_DIR" "$TRUST_DIR" <<'PY'
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(sys.argv[1])), '..'))
from release import trust

key_dir, trust_dir = sys.argv[1], sys.argv[2]
names = sorted(n for n in os.listdir(key_dir) if n.endswith(trust.KEY_SUFFIXES))
if not names:
    sys.stderr.write('ERROR: no .pem keys in {}\n'.format(key_dir))
    raise SystemExit(1)

for name in names:
    with open(os.path.join(key_dir, name), 'rb') as handle:
        pem = handle.read()
    try:
        trust.provision(trust_dir, name, pem)
        print('provisioned {} {}'.format(name, trust.fingerprint(pem)))
    except trust.TrustError as exc:
        sys.stderr.write('ERROR: {}\n'.format(exc))
        raise SystemExit(1)

installed = trust.state(trust_dir)
print('{} trusts {} key(s): {}'.format(
    trust_dir, len(installed), ', '.join(sorted(installed))))
if not installed:
    sys.stderr.write('ERROR: trust store is empty after provisioning\n')
    raise SystemExit(1)
PY
