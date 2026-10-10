"""release.usb_source - a release on a USB stick plugged into this device.

What matters: only a release signed by a trusted key is offered; what it is
offered as (upgrade, rollback, nothing) follows the same counter rules as an
online release; and an install refuses before touching anything unless every
image it loads is the one the release signed.
"""
import base64
import datetime
import hashlib
import json
import os
import sys

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from release import manifest as manifest_mod
from release import usb_source as us
from release import verify as verify_mod

NOW = datetime.datetime(2026, 10, 1)
COMMIT = 'c0ffee' * 6 + 'abcd'


def digest_of(data):
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def keypair(directory):
    key = ec.generate_private_key(ec.SECP256R1())
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'release.pem').write_bytes(key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    return key


def registry_manifest(component):
    return json.dumps({'schemaVersion': 2,
                       'config': {'digest': 'sha256:' + hashlib.sha256(component.encode()).hexdigest()}}).encode()


def write_release(root, key, counter=40, release='2.0', pythons=None):
    """A stick holding one signed release, laid out as release/usb.py writes it."""
    path = root / 'flexrun-releases' / '{}-x86-{}'.format(release, counter)
    (path / 'registry').mkdir(parents=True)
    (path / 'images').mkdir()
    manifests = {c: registry_manifest(c) for c in manifest_mod.FOUNDATIONAL}
    parsed = manifest_mod.build_manifest(
        release, counter, {c: ('dev' if c in manifest_mod.ENV_TAGGED else '1.0') for c in manifest_mod.FOUNDATIONAL},
        COMMIT, resolver=lambda repository, tag: digest_of(manifests[repository.split('-', 1)[1]]),
        now=NOW, arches=('x86',))
    raw = manifest_mod.canonical_bytes(parsed)
    (path / 'manifest.json').write_bytes(raw)
    (path / 'manifest.sig').write_bytes(base64.b64encode(key.sign(raw, ec.ECDSA(hashes.SHA256()))) + b'\n')

    images = {}
    for component, body in manifests.items():
        (path / 'registry' / (component + '.manifest')).write_bytes(body)
        (path / 'images' / (component + '.tar')).write_bytes(b'image')
        images[component] = {'tar': 'images/{}.tar'.format(component),
                             'manifest': 'registry/{}.manifest'.format(component), 'platform': None,
                             'image_id': json.loads(body)['config']['digest']}
    tag = us.python_tag()
    for name in (pythons if pythons is not None else [tag]):
        (path / 'packages' / name).mkdir(parents=True)
    (path / 'flexrun.bundle').write_bytes(b'bundle')
    (path / 'bundle.json').write_text(json.dumps({
        'schema': 'flexrun.usb/v1', 'release': release, 'counter': counter, 'arch': 'x86',
        'flexrun_commit': COMMIT, 'flexrun': 'flexrun.bundle',
        'pythons': pythons if pythons is not None else [tag], 'images': images}))
    return path


@pytest.fixture
def device(tmp_path):
    trust = tmp_path / 'trust'
    key = keypair(trust)
    media = tmp_path / 'media'
    stick = media / 'alec' / 'FV-RELEASE'
    stick.mkdir(parents=True)
    return {'trust': str(trust), 'key': key, 'roots': (str(media),), 'stick': stick}


def state(installed=None, high_water=0, history=()):
    return {'installed': {'counter': installed} if installed else None,
            'high_water': high_water, 'history': [{'counter': c} for c in history]}


class TestFind:

    def _offer(self, device, **kwargs):
        [found] = us.find('x86', state(**kwargs), device['trust'], roots=device['roots'])
        return found

    def test_a_newer_release_is_offered_as_an_upgrade(self, device):
        write_release(device['stick'], device['key'], counter=40)
        found = self._offer(device, installed=38, high_water=38, history=(38,))
        assert (found['offer'], found['release'], found['stick']) == ('upgrade', '2.0', 'FV-RELEASE')

    def test_the_installed_release(self, device):
        write_release(device['stick'], device['key'], counter=38)
        assert self._offer(device, installed=38, high_water=38, history=(38,))['offer'] == 'installed'

    def test_a_release_this_device_ran_is_a_rollback(self, device):
        write_release(device['stick'], device['key'], counter=37)
        assert self._offer(device, installed=38, high_water=38, history=(37, 38))['offer'] == 'rollback'

    def test_an_older_release_never_run_here_is_not_offered(self, device):
        write_release(device['stick'], device['key'], counter=30)
        assert self._offer(device, installed=38, high_water=38, history=(38,))['offer'] == 'older'

    def test_a_release_signed_by_another_key_is_invalid(self, device, tmp_path):
        write_release(device['stick'], keypair(tmp_path / 'other'), counter=40)
        assert self._offer(device, installed=38, high_water=38)['offer'] == 'invalid'

    def test_no_packages_for_this_python_is_unsupported(self, device):
        write_release(device['stick'], device['key'], counter=40, pythons=['py27'])
        found = self._offer(device, installed=38, high_water=38)
        assert found['offer'] == 'unsupported'
        assert 'Python {}.{}'.format(*sys.version_info[:2]) in found['detail']

    def test_nothing_plugged_in(self, device):
        assert us.find('x86', state(), device['trust'], roots=device['roots']) == []


class FakeDocker:
    def __init__(self, loaded_as=None):
        self.loaded_as, self.calls = loaded_as or {}, []

    def __call__(self, cmd):
        self.calls.append(cmd)
        if cmd[:3] == ['docker', 'image', 'inspect']:
            return self.loaded_as.get(cmd[-1], cmd[-1])
        return ''


class TestPrepare:

    def _prepare(self, device, path, docker=None, **kwargs):
        return us.prepare(str(path), 'x86', state(**kwargs), device['trust'], NOW,
                          runner=docker or FakeDocker(), log=lambda *_: None, roots=device['roots'])

    def test_every_image_is_loaded_and_run_by_its_proven_id(self, device):
        path = write_release(device['stick'], device['key'], counter=40)
        docker = FakeDocker()
        parsed, refs, source, packages = self._prepare(device, path, docker, installed=38, high_water=38)
        assert parsed['counter'] == 40
        assert set(refs) == set(manifest_mod.FOUNDATIONAL)
        assert refs['backend'] == json.loads(registry_manifest('backend'))['config']['digest']
        assert source == str(path / 'flexrun.bundle')
        assert packages == str(path / 'packages')
        assert sum(1 for c in docker.calls if c[:2] == ['docker', 'load']) == len(manifest_mod.FOUNDATIONAL)

    def test_a_tampered_registry_manifest_stops_before_loading(self, device):
        path = write_release(device['stick'], device['key'], counter=40)
        (path / 'registry' / 'backend.manifest').write_bytes(registry_manifest('evil'))
        docker = FakeDocker()
        with pytest.raises(us.UsbSourceError, match='does not match the signed release'):
            self._prepare(device, path, docker, installed=38, high_water=38)
        assert not any(c[:2] == ['docker', 'load'] and 'backend' in c[-1] for c in docker.calls)

    def test_an_image_that_loads_as_something_else_is_refused(self, device):
        path = write_release(device['stick'], device['key'], counter=40)
        backend_id = json.loads(registry_manifest('backend'))['config']['digest']
        with pytest.raises(us.UsbSourceError, match='not the signed image'):
            self._prepare(device, path, FakeDocker({backend_id: 'sha256:' + '0' * 64}),
                          installed=38, high_water=38)

    def test_an_unseen_older_release_is_refused(self, device):
        path = write_release(device['stick'], device['key'], counter=30)
        with pytest.raises(verify_mod.VerificationError, match='unseen downgrade'):
            self._prepare(device, path, installed=38, high_water=38)

    def test_a_release_this_device_ran_can_be_returned_to(self, device):
        path = write_release(device['stick'], device['key'], counter=37)
        parsed, _, _, _ = self._prepare(device, path, installed=38, high_water=38, history=(37, 38))
        assert parsed['counter'] == 37

    def test_no_packages_for_this_python_is_refused(self, device):
        path = write_release(device['stick'], device['key'], counter=40, pythons=['py27'])
        with pytest.raises(us.UsbSourceError, match='no packages for Python'):
            self._prepare(device, path, installed=38, high_water=38)

    def test_a_path_that_is_not_on_a_stick_is_refused(self, device, tmp_path):
        path = write_release(tmp_path / 'elsewhere', device['key'], counter=40)
        with pytest.raises(us.UsbSourceError, match='not a release on a mounted stick'):
            self._prepare(device, path, installed=38, high_water=38)
