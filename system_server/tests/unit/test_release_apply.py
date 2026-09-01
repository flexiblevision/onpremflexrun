"""Turning a verified manifest into deploy-script arguments and digests.

The two outputs have to agree: versions decide which containers are torn down,
digests decide what bytes they come back as. A mismatch is an upgrade that
pulls one thing and runs another.
"""
import os

import pytest

from release import apply as a
from release import manifest as m


def entry(repo, tag, digest_char='a'):
    return {'repository': repo, 'tag': tag,
            'digest': 'sha256:' + digest_char * 64,
            'tier': 'foundational', 'provenance': 'built'}


def manifest(arch='x86', **overrides):
    images = {
        'backend': entry('fvonprem/x86-backend', '1.999', 'a'),
        'frontend': entry('fvonprem/x86-frontend', '1.999', 'b'),
        'prediction': entry('fvonprem/x86-prediction', '0.51', 'c'),
        'predictlite': entry('fvonprem/x86-predictlite', '1.999', 'd'),
        'vision': entry('fvonprem/x86-vision', '1.999', 'e'),
        'nodecreator': entry('fvonprem/x86-nodecreator', '1.999', 'f'),
        'visiontools': entry('fvonprem/x86-visiontools', '1.999', '0'),
    }
    images.update(overrides)
    return {'schema': m.SCHEMA, 'release': '1.9.1', 'counter': 1, 'arch': arch,
            'created': '2026-08-31T00:00:00Z', 'notAfter': '2026-11-29T00:00:00Z',
            'flexrun': {'repository': 'x', 'commit': 'a' * 40},
            'images': {arch: images}, 'notes': m.blank_notes()}


class TestArgumentOrder:
    """Two orders exist and confusing them is the bug to prevent:

        upgrade_system.sh          $1..$7 versions, no arch
        system_container_upgrades  $1 $2 $3 $4=arch $5 $6 $7 $8

    upgrade_system.sh inserts the arch itself. Encoding the inner order here
    shifts every version by one and upgrades the backend to the frontend's
    version - which is what this originally did."""

    def test_it_matches_what_the_runner_actually_passes(self):
        """Compared against the runner's own constant rather than a copy, so
        the two cannot drift apart silently."""
        import upgrade_runner
        assert a.ARGUMENT_ORDER == upgrade_runner.VERSION_ARGS

    def test_there_is_no_arch_slot(self):
        got = a.versions_for(manifest(), 'x86')
        assert len(got) == 7
        assert 'x86' not in got

    def test_the_dispatcher_inserts_arch_at_position_four(self):
        """Pins the contract this order depends on."""
        body = open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(a.__file__))),
            'system_server', 'upgrade_system.sh')).read()
        call = [l for l in body.splitlines() if '"$3"' in l and '"$4"' in l]
        assert call, 'could not find the dispatch line'
        assert '"$SYSTEM_ARCH" "$4"' in call[0], call[0]

    def test_each_version_lands_where_its_container_expects_it(self):
        got = a.versions_for(manifest(), 'x86')
        assert got[0] == '1.999'   # backend
        assert got[2] == '0.51'    # prediction, deliberately different


class TestSkippingUnchanged:

    def test_a_component_already_at_the_version_is_skipped(self):
        """'True' tells the script to leave it alone. Passing the tag would
        tear down a working container and rebuild it identically - downtime on
        a line for no upgrade."""
        got = a.versions_for(manifest(), 'x86', current={'backend': '1.999'})
        assert got[0] == a.UP_TO_DATE
        assert got[2] == '0.51'

    def test_a_different_current_version_is_upgraded(self):
        got = a.versions_for(manifest(), 'x86', current={'backend': '1.97'})
        assert got[0] == '1.999'

    def test_a_component_absent_from_the_release_is_skipped(self):
        """visiontools has no arm image. Asking for it would pull a tag that
        does not exist and fail the whole run."""
        doc = manifest(arch='arm')
        del doc['images']['arm']['visiontools']
        got = a.versions_for(doc, 'arm')
        assert got[a.ARGUMENT_ORDER.index('visiontools')] == a.UP_TO_DATE

    def test_changing_lists_only_what_moves(self):
        got = a.plan(manifest(), 'x86',
                     current={'backend': '1.999', 'vision': '1.999'})
        assert 'backend' not in got['changing']
        assert 'frontend' in got['changing']


class TestDigestFile:

    def test_every_component_is_pinned_by_digest(self):
        lines = a.plan_lines(manifest(), 'x86')
        assert len(lines) == 7
        for line in lines:
            name, version, ref = line.split(' ')
            repo, _, digest = ref.partition('@')
            assert digest.startswith('sha256:'), line
            # No tag before the @: a tagged reference here would let the daemon
            # resolve the tag instead of the digest.
            assert ':' not in repo.rsplit('/', 1)[-1], line

    def test_the_format_is_what_pinned_ref_parses(self):
        """deploy_common.sh does: awk '$1 == c { print $3 }' for the ref."""
        lines = a.plan_lines(manifest(), 'x86')
        parsed = {l.split(' ')[0]: l.split(' ')[2] for l in lines}
        assert parsed['backend'] == 'fvonprem/x86-backend@sha256:' + 'a' * 64

    def test_it_writes_atomically(self, tmp_path):
        target = tmp_path / 'plan'
        a.write_plan(manifest(), 'x86', str(target))
        assert target.read_text().splitlines()[0].startswith('backend ')
        # no temp files left behind
        assert [p.name for p in tmp_path.iterdir()] == ['plan']

    def test_it_creates_the_directory(self, tmp_path):
        target = tmp_path / 'deep' / 'plan'
        a.write_plan(manifest(), 'x86', str(target))
        assert os.path.isfile(str(target))

    def test_rewriting_replaces_rather_than_appends(self, tmp_path):
        target = tmp_path / 'plan'
        a.write_plan(manifest(), 'x86', str(target))
        a.write_plan(manifest(), 'x86', str(target))
        assert len(target.read_text().strip().splitlines()) == 7


class TestPlan:

    def test_it_refuses_a_manifest_for_another_arch(self):
        """The signature would still verify - it is a real release, just not
        this device's. Applying it would pull images that do not exist."""
        with pytest.raises(a.ApplyError, match='is for arm but this device is x86'):
            a.plan(manifest(arch='arm'), 'x86')

    def test_versions_and_digests_describe_the_same_release(self, tmp_path):
        target = tmp_path / 'plan'
        got = a.plan(manifest(), 'x86', plan_path=str(target))
        pinned = {l.split(' ')[0]: l.split(' ')[2]
                  for l in target.read_text().strip().splitlines()}
        # backend's version argument and its digest must be the same component
        assert got['versions'][0] == '1.999'
        assert pinned['backend'].startswith('fvonprem/x86-backend@')

    def test_it_carries_the_counter_for_the_state_record(self, tmp_path):
        got = a.plan(manifest(), 'x86', plan_path=str(tmp_path / 'plan'))
        assert got['counter'] == 1 and got['release'] == '1.9.1'

    def test_no_digest_path_writes_nothing(self):
        got = a.plan(manifest(), 'x86')
        assert got['plan_path'] is None
