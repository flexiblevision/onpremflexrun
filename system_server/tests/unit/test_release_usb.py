"""release.usb - a published release, written onto a USB stick.

What matters: only a removable drive is ever written to; the release on the
stick verifies as it would online; every image is tied to its signed digest
before it is written; and a stick pulled out part-way is never a release.
"""
import base64
import hashlib
import json
import os

import pytest

from release import usb


def digest_of(data):
    return 'sha256:' + hashlib.sha256(data).hexdigest()


@pytest.fixture
def stick(tmp_path):
    """A mounted USB stick, as /proc/mounts and sysfs describe one."""
    target = tmp_path / 'media' / 'alec' / 'STICK'
    target.mkdir(parents=True)
    sys_root = tmp_path / 'sys'
    disk = sys_root / 'devices' / 'pci0000:00' / 'usb2' / 'block' / 'sdb'
    (disk / 'sdb1').mkdir(parents=True)
    (disk / 'sdb1' / 'partition').write_text('1')
    (disk / 'removable').write_text('1')
    (sys_root / 'class' / 'block').mkdir(parents=True)
    os.symlink(str(disk / 'sdb1'), str(sys_root / 'class' / 'block' / 'sdb1'))
    mounts = tmp_path / 'mounts'
    mounts.write_text('/dev/nvme0n1p2 / ext4 rw 0 0\n/dev/sdb1 {} exfat rw 0 0\n'.format(target))
    return {'target': str(target), 'mounts': str(mounts), 'sys': str(sys_root), 'disk': disk}


class TestRemovableTarget:

    def test_a_usb_stick_is_accepted(self, stick):
        assert usb.removable_target(stick['target'], stick['mounts'], stick['sys']) == stick['target']

    def test_a_usb_drive_that_says_it_is_fixed_is_still_accepted(self, stick):
        (stick['disk'] / 'removable').write_text('0')
        assert usb.removable_target(stick['target'], stick['mounts'], stick['sys'])

    def test_the_local_disk_is_refused(self, stick, tmp_path):
        local = tmp_path / 'home'
        local.mkdir()
        with pytest.raises(usb.UsbError, match='not on a removable drive'):
            usb.removable_target(str(local), stick['mounts'], stick['sys'])

    def test_a_fixed_non_usb_disk_is_refused(self, stick, tmp_path):
        sys_root = tmp_path / 'sys2'
        disk = sys_root / 'devices' / 'pci0000:00' / 'ata1' / 'block' / 'sdb'
        (disk / 'sdb1').mkdir(parents=True)
        (disk / 'sdb1' / 'partition').write_text('1')
        (disk / 'removable').write_text('0')
        (sys_root / 'class' / 'block').mkdir(parents=True)
        os.symlink(str(disk / 'sdb1'), str(sys_root / 'class' / 'block' / 'sdb1'))
        with pytest.raises(usb.UsbError):
            usb.removable_target(stick['target'], stick['mounts'], str(sys_root))

    def test_a_fat32_stick_is_refused_because_images_exceed_4gb(self, stick):
        mounts = open(stick['mounts']).read().replace(' exfat ', ' vfat ')
        open(stick['mounts'], 'w').write(mounts)
        with pytest.raises(usb.UsbError, match='exFAT'):
            usb.removable_target(stick['target'], stick['mounts'], stick['sys'])

    def test_a_missing_path_is_refused(self, stick):
        with pytest.raises(usb.UsbError, match='not a directory'):
            usb.removable_target('/nope/STICK', stick['mounts'], stick['sys'])


class TestPublishedRelease:

    def test_the_channels_release_by_default(self):
        data = json.load(open(usb.RELEASES_JSON))
        counter, raw, signature = usb.published_release('x86')
        assert counter == data['channels']['x86']['stable']
        assert json.loads(raw)['counter'] == counter
        assert signature

    def test_a_specific_counter(self):
        counter, raw, _ = usb.published_release('x86', counter=37)
        assert counter == 37 and json.loads(raw)['counter'] == 37

    def test_an_unpublished_counter_is_refused(self):
        with pytest.raises(usb.UsbError, match='not published'):
            usb.published_release('x86', counter=99999)


class TestWriteSigned:

    def test_a_published_release_verifies(self, tmp_path):
        counter, raw, signature = usb.published_release('x86')
        parsed = usb.write_signed(str(tmp_path), raw, signature)
        assert parsed['counter'] == counter
        assert (tmp_path / 'manifest.json').read_bytes() == raw

    def test_a_tampered_release_is_refused(self, tmp_path):
        _, raw, signature = usb.published_release('x86')
        tampered = raw.replace(b'"counter":', b'"counter": ', 1)
        with pytest.raises(usb.UsbError, match='does not verify'):
            usb.write_signed(str(tmp_path), tampered, signature)


class FakeRegistry:
    def __init__(self, manifests):
        self.manifests = manifests   # digest -> (bytes, media type)

    def manifest_bytes(self, repository, reference):
        return self.manifests[reference]


def image_manifest(config):
    return json.dumps({'schemaVersion': 2, 'config': {'digest': config}}).encode()


class TestRegistryProof:

    def test_a_single_manifest_names_the_image_id(self):
        raw = image_manifest('sha256:cfg')
        stored, platform, config = usb.registry_proof(
            FakeRegistry({digest_of(raw): (raw, 'application/vnd.docker.distribution.manifest.v2+json')}),
            'fvonprem/x86-backend', digest_of(raw), 'x86')
        assert config == 'sha256:cfg'
        assert stored == raw and platform is None

    def test_bytes_that_do_not_hash_to_the_signed_digest_are_refused(self):
        raw = image_manifest('sha256:cfg')
        with pytest.raises(usb.UsbError, match='different hash'):
            usb.registry_proof(FakeRegistry({'sha256:signed': (raw, '')}),
                               'fvonprem/x86-backend', 'sha256:signed', 'x86')

    def test_a_manifest_list_uses_this_arch(self):
        amd = image_manifest('sha256:amd')
        arm = image_manifest('sha256:arm')
        index = json.dumps({'manifests': [
            {'digest': digest_of(arm), 'platform': {'os': 'linux', 'architecture': 'arm64'}},
            {'digest': digest_of(amd), 'platform': {'os': 'linux', 'architecture': 'amd64'}},
        ]}).encode()
        registry = FakeRegistry({digest_of(index): (index, usb.INDEX_TYPES[0]),
                                 digest_of(amd): (amd, ''), digest_of(arm): (arm, '')})
        stored, platform, config = usb.registry_proof(registry, 'r', digest_of(index), 'x86')
        assert (stored, platform, config) == (index, amd, 'sha256:amd')
        assert usb.registry_proof(registry, 'r', digest_of(index), 'arm')[2] == 'sha256:arm'


class FakeTools:
    """docker and git, as far as the writer uses them."""

    def __init__(self, image_ids, sizes):
        self.image_ids, self.sizes, self.calls = image_ids, sizes, []

    def __call__(self, cmd, cwd=None):
        self.calls.append(cmd)
        if cmd[:3] == ['docker', 'image', 'inspect']:
            ref = cmd[-1]
            return self.image_ids[ref] if cmd[4] == '{{.Id}}' else str(self.sizes[ref])
        if cmd[:2] == ['docker', 'save']:
            open(cmd[3], 'wb').write(b'image')
        if cmd[:3] == ['git', 'bundle', 'create']:
            open(cmd[3], 'wb').write(b'bundle')
        if cmd[:2] == ['git', 'show']:
            return 'Flask==2.3.3'
        return ''


@pytest.fixture
def release(monkeypatch):
    raw = image_manifest('sha256:cfg-backend')
    digest = digest_of(raw)
    parsed = {'release': '2.0', 'counter': 39, 'arch': 'x86',
              'flexrun': {'commit': 'c0ffee' * 6 + 'abcd'},
              'images': {'x86': {'backend': {'repository': 'fvonprem/x86-backend', 'digest': digest}}}}
    monkeypatch.setattr(usb, 'published_release',
                        lambda arch, channel='stable', counter=None, releases_path=None:
                        (39, json.dumps(parsed).encode(), 'sig'))
    monkeypatch.setattr(usb, 'write_signed', lambda root, raw, signature, keys_dir=None: parsed)
    ref = 'fvonprem/x86-backend@' + digest
    return {'registry': FakeRegistry({digest: (raw, '')}), 'ref': ref, 'parsed': parsed}


class TestWriteBundle:

    def _write(self, stick, release, tools):
        return usb.write_bundle(stick['target'], resolver=release['registry'], runner=tools,
                                mounts_file=stick['mounts'], sys_root=stick['sys'], log=lambda *_: None)

    def test_everything_lands_on_the_stick(self, stick, release):
        tools = FakeTools({release['ref']: 'sha256:cfg-backend'}, {release['ref']: 1000})
        root = self._write(stick, release, tools)
        assert root == os.path.join(stick['target'], 'flexrun-releases', '2.0-x86-39')
        index = json.load(open(os.path.join(root, 'bundle.json')))
        assert index['counter'] == 39
        assert index['images']['backend']['image_id'] == 'sha256:cfg-backend'
        assert open(os.path.join(root, 'images', 'backend.tar'), 'rb').read() == b'image'
        stored = open(os.path.join(root, 'registry', 'backend.manifest'), 'rb').read()
        # The device re-hashes these bytes against the signed digest.
        assert digest_of(stored) == release['parsed']['images']['x86']['backend']['digest']
        assert os.path.exists(os.path.join(root, 'flexrun.bundle'))
        assert not [n for n in os.listdir(os.path.join(root, 'images')) if n.endswith('.partial')]
        assert index['pythons'] == ['py310', 'py312', 'py38']
        assert open(os.path.join(root, 'install.sh')).read() == open(usb.INSTALLER).read()
        assert open(os.path.join(root, 'packages', 'requirements.txt')).read() == 'Flask==2.3.3\n'

    def test_packages_are_fetched_per_python_and_written_as_this_user(self, stick, release):
        tools = FakeTools({release['ref']: 'sha256:cfg-backend'}, {release['ref']: 1000})
        self._write(stick, release, tools)
        runs = [c for c in tools.calls if c[:2] == ['docker', 'run']]
        images = sorted(next(a for a in c if a.startswith('python:')) for c in runs)
        assert images == sorted(usb.PYTHONS.values())
        for c in runs:
            assert c[c.index('--user') + 1] == '{}:{}'.format(os.getuid(), os.getgid())
            assert 'setuptools' in c and 'wheel' in c
            assert c[c.index('--platform') + 1] == 'linux/amd64'

    def test_an_image_that_is_not_the_signed_one_stops_the_write(self, stick, release):
        tools = FakeTools({release['ref']: 'sha256:something-else'}, {release['ref']: 1000})
        with pytest.raises(usb.UsbError, match='registry says'):
            self._write(stick, release, tools)
        root = os.path.join(stick['target'], 'flexrun-releases', '2.0-x86-39')
        assert not os.path.exists(os.path.join(root, 'bundle.json'))

    def test_a_stick_too_small_is_refused_before_writing_images(self, stick, release):
        tools = FakeTools({release['ref']: 'sha256:cfg-backend'}, {release['ref']: 10 ** 15})
        with pytest.raises(usb.UsbError, match='GB free'):
            self._write(stick, release, tools)
        assert not any(c[:2] == ['docker', 'save'] for c in tools.calls)

    def test_a_release_already_on_the_stick_is_not_rewritten(self, stick, release):
        tools = FakeTools({release['ref']: 'sha256:cfg-backend'}, {release['ref']: 1000})
        self._write(stick, release, tools)
        with pytest.raises(usb.UsbError, match='already on this stick'):
            self._write(stick, release, tools)

    def test_the_local_disk_is_never_written(self, stick, release, tmp_path):
        local = tmp_path / 'home'
        local.mkdir()
        tools = FakeTools({}, {})
        with pytest.raises(usb.UsbError):
            usb.write_bundle(str(local), resolver=release['registry'], runner=tools,
                             mounts_file=stick['mounts'], sys_root=stick['sys'], log=lambda *_: None)
        assert tools.calls == []
        assert os.listdir(str(local)) == []


def test_the_first_install_script_parses():
    import subprocess
    assert subprocess.run(['sh', '-n', usb.INSTALLER]).returncode == 0
