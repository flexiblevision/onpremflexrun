"""
Releases on a USB stick plugged into this device.

Looked for only when someone opens the release screen - a stick never starts
anything on its own. find() says what is on any mounted stick and what this
device could do with it; prepare() is what an install runs: the signature and
counter checks an online release gets, then every image loaded from the stick
and proved against its signed digest before anything on the device is touched.

The stick's layout is written by release/usb.py.
"""
import glob
import json
import os
import subprocess
import sys

from release import manifest as manifest_mod
from release import verify as verify_mod
from release.usb import INDEX, ROOT_DIR, platform_child, sha256_digest

MEDIA_ROOTS = ('/media', '/run/media')

UPGRADE, ROLLBACK, INSTALLED, OLDER, INVALID, UNSUPPORTED = (
    'upgrade', 'rollback', 'installed', 'older', 'invalid', 'unsupported')


class UsbSourceError(Exception):
    pass


def python_tag():
    return 'py%d%d' % sys.version_info[:2]


def run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise UsbSourceError('{} failed: {}'.format(' '.join(cmd), (result.stderr or result.stdout).strip()))
    return result.stdout.strip()


def candidates(roots=MEDIA_ROOTS):
    """Release directories on mounted sticks: /media/<user>/<label>/... or /media/<label>/..."""
    found = []
    for root in roots:
        for depth in ('*', '*/*'):
            pattern = os.path.join(root, depth, ROOT_DIR, '*', INDEX)
            found.extend(os.path.dirname(p) for p in glob.glob(pattern))
    return sorted(set(found))


def _read(path, name, mode='r'):
    with open(os.path.join(path, name), mode) as handle:
        return handle.read()


def _stick(path):
    """The stick's label, as the operator sees it."""
    parts = os.path.normpath(path).split(os.sep)
    return parts[parts.index(ROOT_DIR) - 1] if ROOT_DIR in parts else path


def describe(path, arch, high_water, known, installed, trust_dir):
    """What one release on a stick is, and what this device would do with it."""
    entry = {'path': path, 'stick': _stick(path), 'release': None, 'counter': None}
    try:
        index = json.loads(_read(path, INDEX))
        raw = _read(path, 'manifest.json', 'rb')
        verify_mod.local_verify_any(os.path.join(path, 'manifest.json'),
                                    os.path.join(path, 'manifest.sig'), trust_dir)
        parsed = manifest_mod.loads(raw)
    except Exception as exc:
        return dict(entry, offer=INVALID, detail='not a release signed by Flexible Vision ({})'.format(exc))

    entry.update(release=parsed['release'], counter=parsed['counter'])
    if parsed.get('arch') != arch or index.get('counter') != parsed['counter']:
        return dict(entry, offer=INVALID, detail='this release is for a different device')
    if python_tag() not in (index.get('pythons') or []):
        return dict(entry, offer=UNSUPPORTED,
                    detail='this release has no packages for Python {}.{}'.format(*sys.version_info[:2]))

    counter = parsed['counter']
    if counter == installed:
        return dict(entry, offer=INSTALLED, detail='already installed')
    if counter in known:
        return dict(entry, offer=ROLLBACK, detail='this device ran it before')
    if counter > high_water:
        return dict(entry, offer=UPGRADE, detail='newer than the installed release')
    return dict(entry, offer=OLDER, detail='older than this device\'s software and never run here')


def find(arch, state, trust_dir, roots=MEDIA_ROOTS):
    """Every release on any mounted stick, newest first."""
    installed = (state.get('installed') or {}).get('counter')
    known = {h.get('counter') for h in state.get('history') or []}
    found = [describe(p, arch, state.get('high_water') or 0, known, installed, trust_dir)
             for p in candidates(roots)]
    return sorted(found, key=lambda e: e.get('counter') or 0, reverse=True)


def prove_image(path, item, signed_digest, arch):
    """The image id the stick's registry manifest names, once its bytes are
    shown to be what the release signed."""
    raw = _read(path, item['manifest'], 'rb')
    if sha256_digest(raw) != signed_digest:
        raise UsbSourceError('{} does not match the signed release'.format(item['manifest']))
    body = json.loads(raw)
    if 'manifests' in body:
        child = platform_child(body, arch)
        platform = _read(path, item.get('platform') or '', 'rb') if child and item.get('platform') else None
        if child is None or platform is None or sha256_digest(platform) != child['digest']:
            raise UsbSourceError('{} has no proven image for {}'.format(item['manifest'], arch))
        body = json.loads(platform)
    return body['config']['digest']


def prepare(path, arch, state, trust_dir, now, runner=run, log=print, roots=MEDIA_ROOTS):
    """(parsed, local image ids, flex-run bundle, packages dir) for an install.

    Refuses before touching the device: a bad signature, an unseen older
    release, no packages for this Python, or an image that is not the signed one.
    """
    if os.path.realpath(path) not in {os.path.realpath(p) for p in candidates(roots)}:
        raise UsbSourceError('{} is not a release on a mounted stick'.format(path))

    index = json.loads(_read(path, INDEX))
    raw = _read(path, 'manifest.json', 'rb')
    installed = (state.get('installed') or {}).get('counter')
    known = [h.get('counter') for h in state.get('history') or []]
    parsed = verify_mod.verify(
        raw, arch, state.get('high_water') or 0, now, installed=installed, known_counters=known,
        signature_path=os.path.join(path, 'manifest.sig'),
        manifest_path=os.path.join(path, 'manifest.json'), public_key_path=trust_dir)

    packages = os.path.join(path, 'packages')
    if not os.path.isdir(os.path.join(packages, python_tag())):
        raise UsbSourceError('this release has no packages for Python {}.{}'.format(*sys.version_info[:2]))
    if index.get('flexrun_commit') != (parsed.get('flexrun') or {}).get('commit'):
        raise UsbSourceError('the stick\'s flex-run is not the commit the release signed')

    local_refs = {}
    for component, entry in sorted(manifest_mod.components_for(parsed, arch).items()):
        item = (index.get('images') or {}).get(component)
        if not item:
            raise UsbSourceError('the stick has no image for {}'.format(component))
        image_id = prove_image(path, item, entry['digest'], arch)
        log('[usb] loading {}'.format(component))
        runner(['docker', 'load', '--quiet', '--input', os.path.join(path, item['tar'])])
        loaded = runner(['docker', 'image', 'inspect', '--format', '{{.Id}}', image_id])
        if loaded != image_id:
            raise UsbSourceError('{} loaded as {} - not the signed image'.format(component, loaded))
        local_refs[component] = image_id

    return parsed, local_refs, os.path.join(path, index.get('flexrun') or 'flexrun.bundle'), packages
