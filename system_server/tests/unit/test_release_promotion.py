"""What is promoted must exist.

releases.py is the storage layer and it is edited by hand, under time pressure,
during a release or a rollback. The failure this catches is a promote that looks
done in git and 404s on every device: a channel pointing at a counter that was
never added to RELEASES, or added under the wrong arch.

Deploying is a separate manual step, so git and the live endpoint can disagree.
Catching it at PR time is the only place it is cheap.
"""
import base64
import importlib.util
import json
import os

import pytest

# tests/unit -> tests -> system_server -> repo root
REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
CLOUDFUNCTION = os.path.join(REPO, 'release', 'cloudfunction')


def load():
    spec = importlib.util.spec_from_file_location(
        'releases_under_test', os.path.join(CLOUDFUNCTION, 'releases.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def releases():
    return load()


class TestShape:

    def test_both_arches_exist_in_both_maps(self, releases):
        """Counters are per arch. A missing arch key is a KeyError inside the
        endpoint, which is a 500 to every device of that architecture."""
        assert set(releases.RELEASES) == set(releases.CHANNELS)
        assert set(releases.RELEASES) >= {'x86', 'arm'}

    def test_counters_are_integers(self, releases):
        for arch, entries in releases.RELEASES.items():
            for counter in entries:
                assert isinstance(counter, int) and not isinstance(counter, bool), \
                    '{} release key {!r} is not an integer'.format(arch, counter)


class TestPromotionsResolve:
    """The headline check: every channel points at something that exists."""

    def test_every_promoted_counter_is_published(self, releases):
        for arch, channels in releases.CHANNELS.items():
            for name, counter in channels.items():
                if counter is None:
                    continue
                assert counter in releases.RELEASES.get(arch, {}), (
                    "CHANNELS['{}']['{}'] = {} but RELEASES['{}'] has {} - "
                    "every device on {} would get a 404".format(
                        arch, name, counter, arch,
                        sorted(releases.RELEASES.get(arch, {})) or 'nothing',
                        arch))

    def test_a_counter_promoted_under_the_wrong_arch_is_caught(self, releases):
        """The mistake this is really guarding: pasting an x86 release and then
        pointing arm's channel at it. Both maps look plausible in isolation."""
        for arch, channels in releases.CHANNELS.items():
            others = [a for a in releases.RELEASES if a != arch]
            for name, counter in channels.items():
                if counter is None or counter in releases.RELEASES.get(arch, {}):
                    continue
                elsewhere = [a for a in others
                             if counter in releases.RELEASES.get(a, {})]
                assert not elsewhere, (
                    "CHANNELS['{}']['{}'] = {} exists only under {} - "
                    "promoted under the wrong architecture".format(
                        arch, name, counter, elsewhere))


class TestPublishedEntriesAreUsable:

    def test_each_entry_has_both_halves(self, releases):
        """A half-pasted entry deploys cleanly and then fails verification on
        the device, which reads as a signing problem rather than a paste one."""
        for arch, entries in releases.RELEASES.items():
            for counter, entry in entries.items():
                for field in ('manifest_b64', 'signature'):
                    assert entry.get(field), \
                        '{} release {} has no {}'.format(arch, counter, field)

    def test_the_manifest_decodes(self, releases):
        for arch, entries in releases.RELEASES.items():
            for counter, entry in entries.items():
                try:
                    raw = base64.b64decode(entry['manifest_b64'], validate=True)
                except Exception as exc:
                    pytest.fail('{} release {} manifest_b64 is not base64: {}'
                                .format(arch, counter, exc))
                assert raw, '{} release {} decodes to nothing'.format(arch, counter)

    def test_the_manifest_matches_the_key_it_is_filed_under(self, releases):
        """RELEASES is keyed by counter and the manifest carries its own. If
        they disagree, the device's anti-rollback compares the wrong number."""
        for arch, entries in releases.RELEASES.items():
            for counter, entry in entries.items():
                parsed = json.loads(
                    base64.b64decode(entry['manifest_b64']).decode('utf-8'))
                assert parsed.get('counter') == counter, (
                    '{} filed under {} but the manifest says {}'
                    .format(arch, counter, parsed.get('counter')))
                assert parsed.get('arch') in (None, arch), (
                    '{} filed under {} but the manifest is for {}'
                    .format(arch, counter, parsed.get('arch')))


class TestNothingSecretIsPasted:
    """This file is edited during an incident and the repository is public."""

    def test_it_holds_only_base64_and_integers(self, releases):
        body = open(os.path.join(CLOUDFUNCTION, 'releases.py')).read()
        for marker in ('BEGIN RSA PRIVATE KEY', 'BEGIN PRIVATE KEY',
                       'BEGIN EC PRIVATE KEY', 'dckr_pat_', 'ghp_'):
            assert marker not in body, 'releases.py contains {}'.format(marker)
