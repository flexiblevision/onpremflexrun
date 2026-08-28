"""Decide whether a release manifest may be applied to this device.

Four independent checks, each closing a different attack or failure:

  signature   the manifest was signed by the release key. A build-server
              compromise can then publish but cannot sign.
  counter     strictly greater than the device's HIGH WATER MARK - the highest
              counter it has ever accepted, not merely what is running. Stops a
              signed but known-bad older release being replayed at a device;
              the signature stays valid forever, so freshness cannot come from
              it.
  arch        the manifest actually covers this machine.

An operator sometimes has to go back, and that is a different actor from an
attacker: authenticated, deliberate, standing at the machine. verify_rollback()
serves that case. It does not *remove* the counter check, it REPLACES it - the
target must be a release this device has actually run. So the worst an operator
can do is return to a state that was live here before, and the automatic path
still refuses anything below the high water mark. Rolling back leaves the high
water mark alone, which is what keeps "a newer release is available" true.

Expiry is REPORTED, not enforced. `notAfter` defends against a *freeze* - an
attacker who can only block traffic keeps a device on old code, and silence is
otherwise indistinguishable from being up to date. But enforcing it means a
quarter without a release refuses updates fleet-wide, which is an outage you
inflict on yourself, and a far likelier event than the attack. The counter
already blocks downgrades, so the residual risk is only a device that has never
seen anything newer - and a human investigating "last saw a release 94 days ago"
handles that. Pass enforce_expiry=True where a customer requires the hard gate.

Signature verification is delegated. The default checks a base64 DER ECDSA
signature with the cryptography package - no cosign on devices, and no
transparency log to reach. Tests inject their own.
"""
import base64
import binascii
import datetime
import os
import subprocess

from . import manifest as manifest_mod


class VerificationError(Exception):
    """A manifest must not be applied."""


def trusted_keys(public_key_path):
    """Resolve a key reference into the list of keys to try.

    Accepts a single PEM file, a directory of *.pem, or a list. A directory is
    the shape to provision: adding a key is dropping a file in, which is what
    makes rotation possible without a code change.
    """
    if isinstance(public_key_path, (list, tuple)):
        paths = [str(entry) for entry in public_key_path]
    elif public_key_path and os.path.isdir(str(public_key_path)):
        directory = str(public_key_path)
        paths = sorted(
            os.path.join(directory, name) for name in os.listdir(directory)
            if name.endswith(('.pem', '.pub')))
    elif public_key_path:
        paths = [str(public_key_path)]
    else:
        paths = []

    if not paths:
        raise VerificationError(
            'no release public key to verify against ({!r}) - a device with an '
            'empty trust store cannot accept any release'
            .format(public_key_path))
    return paths


def local_verify_any(manifest_path, signature_path, public_key_path):
    """Verify against every trusted key; the first that matches wins.

    Returns the key that verified, so a caller can log which one and notice a
    fleet still accepting a key that was supposed to be retired.

    More than one trusted key is what makes rotation possible at all: a device
    that trusts exactly one key can never be moved to another, so losing that
    key strands it and a compromise cannot be revoked.
    """
    paths = trusted_keys(public_key_path)
    failures = []
    for path in paths:
        try:
            local_verify(manifest_path, signature_path, path)
            return path
        except VerificationError as exc:
            failures.append('{}: {}'.format(os.path.basename(path), exc))

    raise VerificationError(
        'signature did not match any of the {} trusted key(s):\n  {}'
        .format(len(paths), '\n  '.join(failures)))


def local_verify(manifest_path, signature_path, public_key_path):
    """Verify a base64 DER ECDSA signature. The default: no cosign on devices.

    cosign 3's verify-blob wants the Sigstore transparency log and needs
    --insecure-ignore-tlog to work offline, which is the wrong shape for a
    factory device with no route out - and the binary is 141MB. The signature is
    plain ECDSA over the manifest bytes, so checking it needs none of that.
    """
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
    except ImportError as exc:
        raise VerificationError(
            'cannot verify a release without the cryptography package: {}'
            .format(exc))

    try:
        with open(public_key_path, 'rb') as handle:
            public_key = serialization.load_pem_public_key(handle.read())
        with open(signature_path, 'rb') as handle:
            signature = base64.b64decode(handle.read().strip())
        with open(manifest_path, 'rb') as handle:
            payload = handle.read()
    except (OSError, ValueError, binascii.Error) as exc:
        raise VerificationError(
            'could not read the manifest, signature or key: {}'.format(exc))

    if not isinstance(public_key, ec.EllipticCurvePublicKey):
        raise VerificationError(
            'release key must be an EC public key, got {}'
            .format(type(public_key).__name__))

    try:
        public_key.verify(signature, payload, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        raise VerificationError(
            'signature verification failed - the manifest is not the one that '
            'was signed, or it was signed by a different key')


def cosign_verify(manifest_path, signature_path, public_key_path):
    """Kept for a machine that has cosign and wants it to be the checker.

    --insecure-ignore-tlog is required, not sloppiness: the release is signed
    with a key rather than a Sigstore identity, and a device has no route to the
    transparency log. local_verify is what devices use.
    """
    result = subprocess.run(
        ['cosign', 'verify-blob',
         '--key', public_key_path,
         '--bundle', signature_path,
         '--insecure-ignore-tlog',
         manifest_path],
        capture_output=True, text=True)
    if result.returncode != 0:
        raise VerificationError(
            'signature verification failed: {}'
            .format((result.stderr or result.stdout or '').strip()[:400]))


def _parse_iso(value, field):
    try:
        return datetime.datetime.strptime(str(value), '%Y-%m-%dT%H:%M:%SZ')
    except (ValueError, TypeError):
        raise VerificationError(
            '{} is not an ISO-8601 UTC timestamp: {!r}'.format(field, value))


def _parsed_and_signed(raw_manifest, signature_path, public_key_path,
                       manifest_path, verifier):
    """Structure, then signature. Nothing downstream reasons about unverified
    fields, and a manifest we cannot parse never reaches a signature check."""
    try:
        parsed = manifest_mod.loads(raw_manifest)
    except manifest_mod.ManifestError as exc:
        raise VerificationError('malformed manifest: {}'.format(exc))

    if verifier is None and signature_path is None:
        raise VerificationError(
            'refusing to verify a manifest with no signature - an unsigned '
            'release is the thing this exists to prevent')

    active = verifier or local_verify_any
    if not signature_path or not public_key_path or not manifest_path:
        raise VerificationError(
            'signature verification requires manifest_path, '
            'signature_path and public_key_path')
    active(manifest_path, signature_path, public_key_path)
    return parsed


def _check_dates_and_arch(parsed, arch, now, enforce_expiry):
    not_after = _parse_iso(parsed['notAfter'], 'notAfter')
    if enforce_expiry and now > not_after:
        raise VerificationError(
            'release {} expired at {} and expiry enforcement is on'
            .format(parsed['release'], parsed['notAfter']))

    created = _parse_iso(parsed['created'], 'created')
    if created > not_after:
        raise VerificationError(
            'manifest is internally inconsistent: created {} is after '
            'notAfter {}'.format(parsed['created'], parsed['notAfter']))

    # Wrapped: callers must only ever have to catch VerificationError.
    try:
        manifest_mod.components_for(parsed, arch)
    except manifest_mod.ManifestError as exc:
        raise VerificationError(str(exc))


def verify(raw_manifest, arch, high_water, now,
           signature_path=None, public_key_path=None, manifest_path=None,
           verifier=None, enforce_expiry=False):
    """The automatic path: what a device accepts from its channel unprompted.

    high_water is the highest counter this device has ever accepted, NOT what
    is currently running. Passing the running counter instead would let a
    remote actor push a device that had rolled back straight back down again.
    """
    parsed = _parsed_and_signed(raw_manifest, signature_path, public_key_path,
                               manifest_path, verifier)

    if not isinstance(high_water, int) or isinstance(high_water, bool):
        raise VerificationError(
            'high_water must be an integer, got {!r}'.format(high_water))

    counter = parsed['counter']
    if counter <= high_water:
        raise VerificationError(
            'release {} has counter {} which is not newer than this device\'s '
            'high water mark {} - refusing a rollback'
            .format(parsed['release'], counter, high_water))

    _check_dates_and_arch(parsed, arch, now, enforce_expiry)
    return parsed


def verify_rollback(raw_manifest, arch, known_counters, now,
                    signature_path=None, public_key_path=None,
                    manifest_path=None, verifier=None, enforce_expiry=False):
    """An operator deliberately going back to a release this device has run.

    The counter requirement is replaced rather than dropped: an unbounded
    choice would let someone walk a device backwards into a release with a
    known vulnerability. "Return to what was working here" is the actual need.
    """
    parsed = _parsed_and_signed(raw_manifest, signature_path, public_key_path,
                               manifest_path, verifier)

    try:
        known = set(int(c) for c in (known_counters or ()))
    except (TypeError, ValueError):
        raise VerificationError(
            'known_counters must be integers, got {!r}'.format(known_counters))

    if not known:
        raise VerificationError(
            'this device has no recorded releases to roll back to')

    counter = parsed['counter']
    if counter not in known:
        raise VerificationError(
            'release {} (counter {}) has never run on this device - rollback '
            'is limited to {}'
            .format(parsed['release'], counter,
                    ', '.join(str(c) for c in sorted(known))))

    _check_dates_and_arch(parsed, arch, now, enforce_expiry)
    return parsed


def is_stale(parsed, now, warn_days=14):
    """True when a valid manifest is close enough to expiry to flag.

    A device that has stopped receiving releases is the signal here; the point
    is to surface it before the manifest expires, since expiry no longer stops
    anything on its own.
    """
    not_after = _parse_iso(parsed['notAfter'], 'notAfter')
    return now + datetime.timedelta(days=warn_days) >= not_after


def freshness(parsed, now, warn_days=14):
    """How old this release is, for the device's status and fleet telemetry.

    This is the replacement for enforcing expiry: a frozen device is *found*
    rather than broken. Whatever surfaces this needs to make `age_days` visible,
    because nothing downstream refuses on it.
    """
    created = _parse_iso(parsed['created'], 'created')
    not_after = _parse_iso(parsed['notAfter'], 'notAfter')
    age = now - created
    remaining = not_after - now
    return {
        'release': parsed.get('release'),
        'counter': parsed.get('counter'),
        'created': parsed.get('created'),
        'notAfter': parsed.get('notAfter'),
        'age_days': age.days,
        'days_until_expiry': remaining.days,
        'expired': now > not_after,
        'stale': is_stale(parsed, now, warn_days),
    }
