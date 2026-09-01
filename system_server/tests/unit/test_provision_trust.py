"""Provisioning the release signing keys onto a device.

A device with no trust store cannot verify a manifest, and it cannot be given
one through an update it has no way to check - so the keys ship in the tree and
are installed before any container starts.
"""
import os
import subprocess

import pytest

from release import trust

REPO = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..'))
SCRIPT = os.path.join(REPO, 'setup', 'provision_trust.sh')
KEY_DIR = os.path.join(REPO, 'release', 'keys')


def run(trust_dir):
    env = dict(os.environ, FLEXRUN_TRUST_DIR=str(trust_dir))
    return subprocess.run([SCRIPT], env=env, capture_output=True, text=True)


class TestShippedKeys:
    """The keys are committed deliberately, past a blanket *.pem ignore rule.
    If they ever stop shipping, every device silently loses the ability to take
    a release - so this is asserted rather than assumed."""

    def test_both_keys_are_in_the_tree(self):
        names = sorted(os.listdir(KEY_DIR))
        assert names == ['release-signing-standby.pem', 'release-signing.pem']

    def test_they_are_public_keys_not_private_ones(self):
        """The whole reason *.pem is ignored by default. A private key here
        would be published to a public repository."""
        for name in os.listdir(KEY_DIR):
            body = open(os.path.join(KEY_DIR, name)).read()
            assert 'PUBLIC KEY' in body
            assert 'PRIVATE KEY' not in body

    def test_the_fingerprints_are_the_ones_KEYS_md_records(self):
        """If these drift, a device trusts something the release engineer is
        not signing with, and every update fails verification."""
        got = {name: trust.fingerprint(
                   open(os.path.join(KEY_DIR, name), 'rb').read())
               for name in os.listdir(KEY_DIR)}
        assert got['release-signing.pem'] == 'e7b6ec363b2142c5'
        assert got['release-signing-standby.pem'] == 'e0a15f7c652d6c23'

    def test_the_standby_ships_too(self):
        """Both keys on every device is what makes rotation possible: switching
        to the standby then needs no trust update, which is the one thing that
        cannot be delivered to a device that has stopped trusting you."""
        keys = set(trust.fingerprint(open(os.path.join(KEY_DIR, n), 'rb').read())
                   for n in os.listdir(KEY_DIR))
        assert len(keys) == 2


@pytest.mark.skipif(not os.access(SCRIPT, os.X_OK), reason='script not executable')
class TestProvisioning:

    def test_a_fresh_device_ends_up_trusting_both(self, tmp_path):
        result = run(tmp_path)
        assert result.returncode == 0, result.stderr
        assert len(trust.state(str(tmp_path))) == 2

    def test_running_it_twice_changes_nothing(self, tmp_path):
        """Upgrades re-run this. A second run must not disturb a working store."""
        run(tmp_path)
        before = trust.state(str(tmp_path))
        result = run(tmp_path)
        assert result.returncode == 0
        assert trust.state(str(tmp_path)) == before

    def test_it_creates_the_directory(self, tmp_path):
        target = tmp_path / 'nested' / 'keys'
        assert run(target).returncode == 0
        assert len(trust.state(str(target))) == 2

    def test_a_swapped_key_is_refused_not_overwritten(self, tmp_path):
        """A silent trust-anchor swap is the failure worth preventing: it would
        make the device accept releases signed by whoever did the swapping."""
        run(tmp_path)
        standby = open(os.path.join(KEY_DIR, 'release-signing-standby.pem')).read()
        (tmp_path / 'release-signing.pem').write_text(standby)

        result = run(tmp_path)
        assert result.returncode != 0
        assert 'already holds a different key' in result.stderr

    def test_the_active_key_verifies_a_real_signed_manifest(self, tmp_path):
        """End to end against the artifacts of an actual cut, if one is here.
        Proves the shipped key matches the key that signs."""
        work = os.path.join(REPO, '.release-work')
        manifest = os.path.join(work, 'manifest.json')
        signature = manifest + '.sig'
        if not (os.path.isfile(manifest) and os.path.isfile(signature)):
            pytest.skip('no local cut to verify against')

        run(tmp_path)
        from release import verify as verify_mod
        which = verify_mod.local_verify_any(manifest, signature, str(tmp_path))
        assert which.endswith('release-signing.pem'), \
            'verified by {} - expected the active key'.format(which)
