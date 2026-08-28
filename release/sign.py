"""Sign a prepared release.

Three refusals before the key is touched: bytes must be canonical (the
signature covers bytes, so non-reproducible bytes make a signature
uncomparable), notes_shortfall must be empty (prepare.py is the convenience
gate, this is the wall), and the signer must type the release version back
after seeing the digests.

Crypto is delegated to cosign, matching verify.py. This lives in the repo
rather than beside the key because it must canonicalise identically to
release/manifest.py; the secret is the key, not the code.
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile

from . import manifest as manifest_mod

SIGNATURE_SUFFIX = '.sig'


class SignError(Exception):
    pass


# --- what the signer is shown -----------------------------------------------

def _short(digest):
    return str(digest).replace('sha256:', '')[:12]


def confirmation_text(parsed, arch='x86'):
    """What the signer attests to. Digests are the point - tags are advisory."""
    notes = parsed.get('notes') or {}
    changed = {entry['component'] for entry in (notes.get('changed') or [])
               if isinstance(entry, dict) and 'component' in entry}
    ungated = set(manifest_mod.ungated(parsed, arch))

    lines = [
        'release   {}   counter {}'.format(parsed.get('release'),
                                           parsed.get('counter')),
        'flexrun   {}'.format((parsed.get('flexrun') or {}).get('commit', '?')),
        'created   {}'.format(parsed.get('created', '?')),
        '',
        'summary   {}'.format(notes.get('summary', '')),
        'impact    {}'.format(notes.get('impact', '')),
        'security  {}'.format('YES' if notes.get('security') else 'no'),
        '',
        'images ({}):'.format(arch),
    ]

    for name, image in sorted(manifest_mod.components_for(parsed, arch).items()):
        marks = []
        if name in changed:
            marks.append('CHANGED')
        if name in ungated:
            marks.append('not test-gated')
        lines.append('  {:<12} {:<10} {}{}'.format(
            name, image.get('tag', '?'), _short(image.get('digest')),
            '   <- ' + ', '.join(marks) if marks else ''))

    features = manifest_mod.features_for(parsed, arch)
    if features:
        lines.append('')
        lines.append('features:')
        for name in sorted(features):
            lines.append('  {:<12} {:<10} {}'.format(
                name, features[name].get('tag', '?'),
                _short(features[name].get('digest'))))

    if ungated:
        lines += ['',
                  'NOTE: no test suite gated {} - pinned by'
                  .format(', '.join(sorted(ungated))),
                  '      digest only. Signing vouches for the bytes, not for '
                  'their behaviour.']
    return '\n'.join(lines)


def default_confirm(text, expected_release):
    """Type the release version back. A y/n can be answered without reading."""
    sys.stderr.write(text + '\n\n')
    sys.stderr.write(
        'Type the release version to sign it, anything else to abort: ')
    sys.stderr.flush()
    try:
        typed = sys.stdin.readline()
    except (KeyboardInterrupt, EOFError):
        return False
    return typed.strip() == str(expected_release)


# --- the refusals -----------------------------------------------------------

def check_signable(raw_manifest):
    """Parse, then refuse anything that must not be signed. Returns the parsed
    manifest."""
    raw = raw_manifest.encode('utf-8') if isinstance(raw_manifest, str) \
        else raw_manifest

    parsed = manifest_mod.loads(raw)
    manifest_mod.validate(parsed)

    if manifest_mod.canonical_bytes(parsed) != raw:
        raise SignError(
            'refusing to sign non-canonical bytes - the signature covers the '
            'exact file, so a manifest that is not canonical cannot be '
            'reproduced or compared later. Run it through release.prepare and '
            'sign what that writes.')

    missing = manifest_mod.notes_shortfall(parsed)
    if missing:
        raise SignError(
            'refusing to sign a release with incomplete notes:\n'
            + '\n'.join('  - ' + item for item in missing)
            + '\nThese notes are what an operator reads before accepting an '
              'update on a running line.')

    return parsed


# --- the crypto delegate ----------------------------------------------------

def cosign_sign(manifest_path, key_ref, run=None, bundle_path=None):
    """Delegate to cosign 3.x. key_ref passes through untouched so pkcs11:/kms:
    URIs work like a file path.

    cosign 3 dropped --output-signature and only emits a bundle, so the raw
    signature is read back out of it. What ships stays a base64 DER ECDSA
    signature, which is what a device can check with no cosign installed.

    Note that sign-blob in 3.x always contacts Rekor and a timestamp authority:
    signing needs network, and the manifest's digest is published to a public
    log. Use openssl_signer for an offline key.
    """
    runner = run or (lambda argv: subprocess.run(
        argv, capture_output=True, check=False))

    handle = tempfile.NamedTemporaryFile(suffix='.bundle', delete=False)
    handle.close()
    bundle = bundle_path or handle.name
    try:
        result = runner(['cosign', 'sign-blob', '--key', key_ref,
                         '--bundle', bundle, '--new-bundle-format', '-y',
                         manifest_path])
        if result.returncode != 0:
            stderr = result.stderr or b''
            if isinstance(stderr, str):
                stderr = stderr.encode('utf-8')
            raise SignError('cosign failed: {}'.format(
                stderr.decode('utf-8', 'replace').strip()[:400]))

        try:
            with open(bundle) as opened:
                parsed = json.load(opened)
        except (OSError, ValueError) as exc:
            raise SignError('could not read the cosign bundle: {}'.format(exc))

        signature = (parsed.get('messageSignature') or {}).get('signature')
        if not signature:
            raise SignError(
                'cosign bundle has no messageSignature.signature - this is '
                'written for the v0.3 bundle format')
        return signature.strip().encode('ascii') + b'\n'
    finally:
        if not bundle_path:
            try:
                os.unlink(handle.name)
            except OSError:
                pass


KMS_PREFIX = 'gcpkms://'
_KMS_PARTS = ('projects', 'locations', 'keyRings', 'cryptoKeys', 'versions')


def parse_kms_key(key_ref):
    """gcpkms://projects/P/locations/L/keyRings/R/cryptoKeys/K/versions/V

    cosign's URI shape, so one key reference works whichever signer is used.
    """
    if not str(key_ref or '').startswith(KMS_PREFIX):
        raise SignError('not a Cloud KMS key reference: {!r}'.format(key_ref))

    parts = key_ref[len(KMS_PREFIX):].strip('/').split('/')
    if len(parts) != 10:
        raise SignError(
            'malformed KMS key reference - expected {}<{}>'.format(
                KMS_PREFIX, '/'.join('{}/NAME'.format(p) for p in _KMS_PARTS)))

    fields = {}
    for index, label in enumerate(_KMS_PARTS):
        if parts[index * 2] != label:
            raise SignError(
                'malformed KMS key reference: expected {!r} at position {}, '
                'got {!r}'.format(label, index * 2 + 1, parts[index * 2]))
        if not parts[index * 2 + 1]:
            raise SignError('KMS key reference has an empty {}'.format(label))
        fields[label] = parts[index * 2 + 1]
    return fields


def kms_signer(manifest_path, key_ref, run=None):
    """Sign with Cloud KMS. The private key never leaves Google's HSM.

    gcloud digests locally and sends only the hash, and for an
    ec-sign-p256-sha256 key the signature comes back DER encoded - the same
    thing cosign and openssl produce, so nothing on the device changes.

    Every call is recorded in Cloud Audit Logs, which a file key cannot give
    you. Note that KMS data-access logging is OFF by default; without it there
    is no record of who signed what.
    """
    fields = parse_kms_key(key_ref)
    runner = run or (lambda argv: subprocess.run(
        argv, capture_output=True, check=False))

    handle = tempfile.NamedTemporaryFile(suffix='.sig', delete=False)
    handle.close()
    try:
        result = runner([
            'gcloud', 'kms', 'asymmetric-sign',
            '--project', fields['projects'],
            '--location', fields['locations'],
            '--keyring', fields['keyRings'],
            '--key', fields['cryptoKeys'],
            '--version', fields['versions'],
            '--digest-algorithm', 'sha256',
            '--input-file', manifest_path,
            '--signature-file', handle.name,
        ])
        if result.returncode != 0:
            stderr = result.stderr or b''
            if isinstance(stderr, str):
                stderr = stderr.encode('utf-8')
            raise SignError('gcloud kms asymmetric-sign failed: {}'.format(
                stderr.decode('utf-8', 'replace').strip()[:400]))

        with open(handle.name, 'rb') as opened:
            der = opened.read()
        if not der:
            raise SignError('Cloud KMS produced an empty signature')
        return base64.b64encode(der) + b'\n'
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass


def kms_public_key(key_ref, out_path, run=None):
    """Fetch the PEM public key. This is what gets provisioned onto devices."""
    fields = parse_kms_key(key_ref)
    runner = run or (lambda argv: subprocess.run(
        argv, capture_output=True, check=False))
    result = runner([
        'gcloud', 'kms', 'keys', 'versions', 'get-public-key',
        fields['versions'],
        '--project', fields['projects'],
        '--location', fields['locations'],
        '--keyring', fields['keyRings'],
        '--key', fields['cryptoKeys'],
        '--output-file', out_path,
    ])
    if result.returncode != 0:
        stderr = result.stderr or b''
        if isinstance(stderr, str):
            stderr = stderr.encode('utf-8')
        raise SignError('could not fetch the KMS public key: {}'.format(
            stderr.decode('utf-8', 'replace').strip()[:400]))
    return out_path


def signer_for(key_ref):
    """Pick a delegate from the key reference.

    gcpkms://...                 Cloud KMS
    a cosign key (encrypted)     cosign, which needs network for Rekor
    anything else (EC PEM)       openssl, fully offline
    """
    reference = str(key_ref or '')
    if reference.startswith(KMS_PREFIX):
        return kms_signer
    try:
        with open(reference, 'rb') as handle:
            head = handle.read(64)
    except OSError:
        return cosign_sign
    if b'SIGSTORE PRIVATE KEY' in head:
        return cosign_sign
    return openssl_signer


def openssl_signer(manifest_path, key_ref, run=None):
    """Sign with a standard EC PEM key. No network, no transparency log.

    cosign's own private key is an ENCRYPTED SIGSTORE PRIVATE KEY that openssl
    cannot read, so this wants a key made with openssl ecparam. The output is
    the same base64 DER ECDSA signature cosign_sign returns.
    """
    runner = run or (lambda argv: subprocess.run(
        argv, capture_output=True, check=False))
    result = runner(['openssl', 'dgst', '-sha256', '-sign', key_ref,
                     manifest_path])
    if result.returncode != 0:
        stderr = result.stderr or b''
        if isinstance(stderr, str):
            stderr = stderr.encode('utf-8')
        raise SignError('openssl failed: {}'.format(
            stderr.decode('utf-8', 'replace').strip()[:400]))
    der = result.stdout or b''
    if isinstance(der, str):
        der = der.encode('utf-8')
    if not der:
        raise SignError('openssl produced an empty signature')
    return base64.b64encode(der) + b'\n'


# --- the whole step ---------------------------------------------------------

def sign(raw_manifest, manifest_path, key_ref, confirm=default_confirm,
         signer=None, arch='x86'):
    """Returns the signature bytes, or raises. Never writes anything."""
    parsed = check_signable(raw_manifest)

    if not confirm(confirmation_text(parsed, arch), parsed.get('release')):
        raise SignError('aborted - nothing was signed')

    return (signer or signer_for(key_ref))(manifest_path, key_ref)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Sign a prepared release manifest.')
    parser.add_argument('manifest')
    parser.add_argument('--key', required=True,
                        help='cosign key: a file, or a pkcs11:/kms: URI for a '
                             'hardware token')
    parser.add_argument('--arch', default='x86',
                        help='which architecture to show digests for')
    parser.add_argument('--out', help='signature path (default: <manifest>.sig)')
    parser.add_argument('--force', action='store_true',
                        help='overwrite an existing signature')
    args = parser.parse_args(argv)

    out = args.out or (args.manifest + SIGNATURE_SUFFIX)
    if os.path.exists(out) and not args.force:
        sys.stderr.write(
            '{} already exists - a second signature over the same release is '
            'almost always a mistake. Pass --force if you mean it.\n'.format(out))
        return 1

    try:
        with open(args.manifest, 'rb') as handle:
            raw = handle.read()
        signature = sign(raw, args.manifest, args.key, arch=args.arch)
    except (SignError, manifest_mod.ManifestError) as exc:
        sys.stderr.write('{}\n'.format(exc))
        return 1
    except OSError as exc:
        sys.stderr.write('could not read {}: {}\n'.format(args.manifest, exc))
        return 1

    with open(out, 'wb') as handle:
        handle.write(signature)

    sys.stderr.write('\nsigned -> {}\n'.format(out))
    sys.stderr.write(
        'verify locally before publishing:\n'
        '  cosign verify-blob --key <pub.pem> --signature {} {}\n'
        .format(out, args.manifest))
    return 0


if __name__ == '__main__':
    sys.exit(main())
