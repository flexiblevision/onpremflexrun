"""The device's trust store: which public keys may authorise a release.

A directory of PEM files rather than one file, because a device that trusts
exactly one key can never be moved to another. Adding a key is dropping a file
in; that is the whole mechanism, and it cannot be retrofitted after devices are
provisioned.

Lives OUTSIDE the flex-run tree on purpose. upgrade_flex_run.sh copies a fresh
clone over the live tree, so a trust anchor stored in the repo could be replaced
by anyone who can push to it - which is the circularity the commit pin exists to
close, reopened at the root.

One rule matters more than the rest: an update may never empty the store. A
device with no trusted key cannot accept any release, including the one that
would fix it, and that is the only way this design can strand a device.
"""
import hashlib
import os
import tempfile

DEFAULT_TRUST_DIR = '/etc/flexrun/keys'
KEY_SUFFIXES = ('.pem', '.pub')


class TrustError(Exception):
    pass


def fingerprint(pem_bytes):
    """Short stable id for a public key.

    Taken over the DER SubjectPublicKeyInfo rather than the PEM text, so
    re-wrapping or a changed trailing newline does not change the id.
    """
    try:
        from cryptography.hazmat.primitives import serialization
    except ImportError as exc:
        raise TrustError('cryptography is required to read keys: {}'.format(exc))

    try:
        key = serialization.load_pem_public_key(pem_bytes)
        der = key.public_bytes(serialization.Encoding.DER,
                               serialization.PublicFormat.SubjectPublicKeyInfo)
    except (ValueError, TypeError) as exc:
        raise TrustError('not a PEM public key: {}'.format(exc))
    return hashlib.sha256(der).hexdigest()[:16]


def key_paths(directory=DEFAULT_TRUST_DIR):
    if not directory or not os.path.isdir(str(directory)):
        return []
    return sorted(
        os.path.join(str(directory), name)
        for name in os.listdir(str(directory))
        if name.endswith(KEY_SUFFIXES))


def state(directory=DEFAULT_TRUST_DIR):
    """{fingerprint: filename} for everything the device currently trusts.

    This is what makes "has the fleet picked up the new key yet" answerable,
    which is what step 3 of a rotation depends on.
    """
    found = {}
    for path in key_paths(directory):
        try:
            with open(path, 'rb') as handle:
                found[fingerprint(handle.read())] = os.path.basename(path)
        except (OSError, TrustError):
            continue
    return found


def _atomic_write(path, data):
    directory = os.path.dirname(path) or '.'
    handle = tempfile.NamedTemporaryFile(dir=directory, delete=False)
    try:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.chmod(handle.name, 0o644)
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def provision(directory, name, pem_bytes):
    """Install the first key, or add one. Refuses to overwrite a different key
    under the same name - a silent trust-anchor swap is the thing to avoid."""
    fingerprint(pem_bytes)

    if not name.endswith(KEY_SUFFIXES):
        raise TrustError(
            'key file must end in {} so it is picked up'.format(
                ' or '.join(KEY_SUFFIXES)))

    if not os.path.isdir(directory):
        os.makedirs(directory, mode=0o755)

    path = os.path.join(directory, name)
    if os.path.exists(path):
        with open(path, 'rb') as handle:
            existing = handle.read()
        if fingerprint(existing) != fingerprint(pem_bytes):
            raise TrustError(
                '{} already holds a different key - remove it deliberately '
                'rather than overwriting a trust anchor'.format(name))
        return path

    _atomic_write(path, pem_bytes)
    return path


def apply_update(directory, add=(), remove=(), dry_run=False):
    """Add and/or remove trusted keys, refusing to empty the store.

    add     iterable of (filename, pem_bytes)
    remove  iterable of fingerprints

    Returns the resulting {fingerprint: filename}.
    """
    current = state(directory)

    additions = {}
    for name, pem in add:
        additions[fingerprint(pem)] = (name, pem)

    removals = set(remove)
    unknown = sorted(removals - set(current))
    if unknown:
        raise TrustError(
            'cannot remove key(s) this device does not trust: {}'
            .format(', '.join(unknown)))

    resulting = set(current) | set(additions)
    resulting -= removals
    if not resulting:
        raise TrustError(
            'refusing to leave this device with no trusted key - it could not '
            'then accept any release, including one that would fix it')

    if dry_run:
        return {fp: (additions[fp][0] if fp in additions else current[fp])
                for fp in sorted(resulting)}

    for fp, (name, pem) in additions.items():
        if fp not in current:
            provision(directory, name, pem)

    for fp in removals:
        path = os.path.join(directory, current[fp])
        try:
            os.unlink(path)
        except OSError as exc:
            raise TrustError('could not remove {}: {}'.format(path, exc))

    return state(directory)


def summary(directory=DEFAULT_TRUST_DIR):
    """For the device's own status endpoint."""
    found = state(directory)
    return {
        'directory': str(directory),
        'count': len(found),
        'keys': [{'fingerprint': fp, 'file': found[fp]} for fp in sorted(found)],
    }
