"""
Write a published release onto a USB stick, for a device with no route to the
release service.

    python3 -m release.usb /media/$USER/STICK                # what stable is, x86
    python3 -m release.usb /media/$USER/STICK --channel beta
    python3 -m release.usb /media/$USER/STICK --counter 39 --arch arm

Nothing is cut or signed here. The stick carries a release that is already
published, with its signature, and a device checks it as it would online. The
images are several GB, so nothing large is written anywhere but the stick, and
the target must be a removable drive.

Under flexrun-releases/<release>-<arch>-<counter>/ on the stick:

  manifest.json, manifest.sig   the signed release, byte for byte
  images/<component>.tar        docker save of each pinned image
  registry/<component>.manifest the registry manifest the signed digest names,
                                byte for byte. Its sha256 is that digest and its
                                config digest is the id docker load gives the
                                image, which ties a loaded image to the
                                signature. A manifest list also carries
                                <component>.platform, the one for this arch.
  flexrun.bundle                git bundle of flex-run at the pinned commit;
                                git checks every object against its hash
  bundle.json                   what is where - written last, so a stick pulled
                                out part-way is never taken for a release
"""
import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys

from release import manifest as manifest_mod
from release import verify as verify_mod

ROOT_DIR = 'flexrun-releases'
INDEX = 'bundle.json'
BUNDLE_SCHEMA = 'flexrun.usb/v1'
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELEASES_JSON = os.path.join(REPO, 'release', 'cloudfunction', 'releases.json')
KEYS_DIR = os.path.join(REPO, 'release', 'keys')
PLATFORMS = {'x86': ('linux', 'amd64'), 'arm': ('linux', 'arm64')}
INDEX_TYPES = ('application/vnd.docker.distribution.manifest.list.v2+json',
               'application/vnd.oci.image.index.v1+json')
# docker save writes layers uncompressed, so the tar is about the image size.
SPACE_MARGIN = 1.05


class UsbError(Exception):
    pass


def run(cmd, cwd=None):
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise UsbError('{} failed: {}'.format(' '.join(cmd), (result.stderr or result.stdout).strip()))
    return result.stdout.strip()


def sha256_digest(data):
    return 'sha256:' + hashlib.sha256(data).hexdigest()


# --- the target -------------------------------------------------------------

def _mount_of(path, mounts_file):
    """(mount point, source device) of the filesystem holding path."""
    path = os.path.realpath(path)
    best = None
    with open(mounts_file) as handle:
        for line in handle:
            fields = line.split()
            if len(fields) < 2:
                continue
            source, point = fields[0], fields[1].replace('\\040', ' ')
            if path == point or path.startswith(point.rstrip('/') + '/'):
                if best is None or len(point) > len(best[0]):
                    best = (point, source)
    return best


def _is_removable(device, sys_root):
    """A USB or removable-flagged disk, judged from sysfs."""
    if not device.startswith('/dev/'):
        return False
    name = os.path.basename(os.path.realpath(device))
    block = os.path.join(sys_root, 'class', 'block', name)
    if not os.path.exists(block):
        return False
    disk = os.path.realpath(block)
    if os.path.exists(os.path.join(disk, 'partition')):
        disk = os.path.dirname(disk)
    try:
        with open(os.path.join(disk, 'removable')) as handle:
            if handle.read().strip() == '1':
                return True
    except OSError:
        pass
    # USB SSDs often report removable=0; the bus is what matters here.
    return '/usb' in disk


def removable_target(path, mounts_file='/proc/mounts', sys_root='/sys'):
    if not os.path.isdir(path):
        raise UsbError('{} is not a directory - mount the USB stick and give its path'.format(path))
    found = _mount_of(path, mounts_file)
    if not found or not _is_removable(found[1], sys_root):
        raise UsbError(
            '{} is not on a removable drive. Releases are several GB and are '
            'written only to a USB stick.'.format(path))
    return found[0]


# --- the release ------------------------------------------------------------

def published_release(arch, channel='stable', counter=None, releases_path=RELEASES_JSON):
    """(counter, raw manifest bytes, signature) as the release service serves it."""
    with open(releases_path) as handle:
        data = json.load(handle)
    entries = (data.get('releases') or {}).get(arch) or {}
    if counter is None:
        counter = (data.get('channels') or {}).get(arch, {}).get(channel)
        if counter is None:
            raise UsbError('nothing is promoted to {} for {}'.format(channel, arch))
    entry = entries.get(str(counter))
    if not entry:
        raise UsbError('release {} is not published for {}'.format(counter, arch))
    return int(counter), base64.b64decode(entry['manifest_b64']), entry['signature']


def write_signed(directory, raw, signature, keys_dir=KEYS_DIR):
    """Write the signed pair and check it - a stick carrying a release that
    would fail on a device is worse than no stick."""
    manifest_path = os.path.join(directory, 'manifest.json')
    signature_path = os.path.join(directory, 'manifest.sig')
    with open(manifest_path, 'wb') as handle:
        handle.write(raw)
    with open(signature_path, 'w') as handle:
        handle.write(signature)
    try:
        verify_mod.local_verify_any(manifest_path, signature_path, keys_dir)
        return manifest_mod.loads(raw)
    except (verify_mod.VerificationError, manifest_mod.ManifestError) as exc:
        raise UsbError('release does not verify: {}'.format(exc))


def platform_child(body, arch):
    """The entry for this arch in a manifest list, or None."""
    os_name, cpu = PLATFORMS[arch]
    return next((m for m in body.get('manifests') or []
                 if (m.get('platform') or {}).get('os') == os_name
                 and (m.get('platform') or {}).get('architecture') == cpu), None)


def registry_proof(resolver, repository, digest, arch):
    """(manifest bytes, per-arch manifest bytes or None, the image's config digest).

    A manifest list names one manifest per platform; the image id is the
    config digest of the one for this arch.
    """
    raw, media_type = resolver.manifest_bytes(repository, digest)
    if sha256_digest(raw) != digest:
        raise UsbError('registry served {}@{} with a different hash'.format(repository, digest))
    body = json.loads(raw)
    if media_type in INDEX_TYPES or 'manifests' in body:
        child = platform_child(body, arch)
        if not child:
            raise UsbError('{}@{} has no {} image'.format(repository, digest, arch))
        child_raw, _ = resolver.manifest_bytes(repository, child['digest'])
        if sha256_digest(child_raw) != child['digest']:
            raise UsbError('registry served {}@{} with a different hash'.format(repository, child['digest']))
        return raw, child_raw, json.loads(child_raw)['config']['digest']
    return raw, None, body['config']['digest']


def flexrun_bundle(commit, path, repo=REPO, runner=run):
    """A git bundle of flex-run at the release's commit."""
    try:
        runner(['git', 'cat-file', '-e', commit + '^{commit}'], cwd=repo)
    except UsbError:
        runner(['git', 'fetch', 'origin', commit], cwd=repo)
    ref = 'refs/flexrun-usb/' + commit
    runner(['git', 'update-ref', ref, commit], cwd=repo)
    try:
        runner(['git', 'bundle', 'create', path, ref], cwd=repo)
    finally:
        runner(['git', 'update-ref', '-d', ref], cwd=repo)


def write_bundle(target, arch='x86', channel='stable', counter=None, resolver=None,
                 runner=run, releases_path=RELEASES_JSON, keys_dir=KEYS_DIR,
                 mounts_file='/proc/mounts', sys_root='/sys', log=print):
    removable_target(target, mounts_file, sys_root)
    counter, raw, signature = published_release(arch, channel, counter, releases_path)
    peek = json.loads(raw)
    name = '{}-{}-{}'.format(peek.get('release'), arch, counter)
    root = os.path.join(target, ROOT_DIR, name)
    if os.path.exists(os.path.join(root, INDEX)):
        raise UsbError('release {} is already on this stick at {}'.format(peek.get('release'), root))
    os.makedirs(os.path.join(root, 'images'), exist_ok=True)
    os.makedirs(os.path.join(root, 'registry'), exist_ok=True)

    parsed = write_signed(root, raw, signature, keys_dir)
    images = parsed['images'][arch]
    log('release {} (counter {}) for {}: {} images'.format(parsed['release'], counter, arch, len(images)))

    index_images = {}
    for component, entry in sorted(images.items()):
        ref = '{}@{}'.format(entry['repository'], entry['digest'])
        raw_manifest, platform_manifest, config = registry_proof(
            resolver, entry['repository'], entry['digest'], arch)
        with open(os.path.join(root, 'registry', component + '.manifest'), 'wb') as handle:
            handle.write(raw_manifest)
        if platform_manifest is not None:
            with open(os.path.join(root, 'registry', component + '.platform'), 'wb') as handle:
                handle.write(platform_manifest)
        log('  pulling {}'.format(ref))
        runner(['docker', 'pull', ref])
        image_id = runner(['docker', 'image', 'inspect', '--format', '{{.Id}}', ref])
        if image_id != config:
            raise UsbError('{} pulled as {} but the registry says {}'.format(ref, image_id, config))
        size = int(runner(['docker', 'image', 'inspect', '--format', '{{.Size}}', ref]))
        index_images[component] = {'reference': ref, 'image_id': image_id, 'size': size,
                                   'tar': 'images/{}.tar'.format(component),
                                   'manifest': 'registry/{}.manifest'.format(component),
                                   'platform': ('registry/{}.platform'.format(component)
                                                if platform_manifest is not None else None)}

    needed = sum(i['size'] for i in index_images.values()) * SPACE_MARGIN
    free = shutil.disk_usage(root).free
    if needed > free:
        raise UsbError('the stick needs {:.1f} GB free and has {:.1f} GB'
                       .format(needed / 1e9, free / 1e9))

    for component, item in sorted(index_images.items()):
        final = os.path.join(root, item['tar'])
        partial = final + '.partial'
        log('  writing {} ({:.1f} GB)'.format(item['tar'], item['size'] / 1e9))
        runner(['docker', 'save', '--output', partial, item['reference']])
        os.replace(partial, final)

    commit = parsed['flexrun']['commit']
    log('  writing flex-run {}'.format(commit[:12]))
    flexrun_bundle(commit, os.path.join(root, 'flexrun.bundle'), runner=runner)

    index = {'schema': BUNDLE_SCHEMA, 'release': parsed['release'], 'counter': counter,
             'arch': arch, 'flexrun_commit': commit, 'flexrun': 'flexrun.bundle',
             'images': index_images}
    with open(os.path.join(root, INDEX + '.partial'), 'w') as handle:
        json.dump(index, handle, indent=2, sort_keys=True)
    os.replace(os.path.join(root, INDEX + '.partial'), os.path.join(root, INDEX))
    log('done: {}'.format(root))
    return root


def main(argv=None):
    parser = argparse.ArgumentParser(description='Write a published release onto a USB stick.')
    parser.add_argument('target', help='where the USB stick is mounted, e.g. /media/$USER/STICK')
    parser.add_argument('--arch', default='x86', choices=sorted(PLATFORMS))
    which = parser.add_mutually_exclusive_group()
    which.add_argument('--channel', default='stable', choices=('stable', 'beta'))
    which.add_argument('--counter', type=int, help='a specific published release')
    args = parser.parse_args(argv)

    from release.registry import DockerHubResolver
    try:
        write_bundle(args.target, arch=args.arch, channel=args.channel, counter=args.counter,
                     resolver=DockerHubResolver(use_docker_config=True))
    except UsbError as exc:
        print('error: {}'.format(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
