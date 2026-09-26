"""Choosing what to promote automatically.

This removes the typing, not the decision - so the tests are mostly about the
ways an automatic choice could quietly pick something worse than the pin it
replaces.
"""
import io

import pytest

from release import candidates as c
from release import provenance as p

REV = 'org.opencontainers.image.revision'
SHA = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4'


def labelled(*built):
    """fetch_labels that reports only the named tags as CI-built."""
    return lambda repo, tag: {REV: SHA} if tag in built else {}


class TestVersionOrdering:

    def test_numeric_components_not_string_order(self):
        """The case that matters: 1.10 is newer than 1.9, but sorts below it as
        a string - so a string sort would pin the older image and look right."""
        assert c.version_key('1.10') > c.version_key('1.9')
        assert c.version_tags(['1.9', '1.10', '1.2'])[0] == '1.10'

    def test_differing_depths_compare(self):
        assert c.version_key('1.9.4') > c.version_key('1.9')
        assert c.version_tags(['1.9', '1.9.4'])[0] == '1.9.4'

    def test_commit_tags_are_not_versions(self):
        """CI pushes :<12-hex-sha> on every master commit. Treating one as a
        version would pin an arbitrary build."""
        assert c.version_tags(['1.2', 'a1b2c3d4e5f6', '0f1e2d3c4b5a']) == ['1.2']

    def test_channels_are_not_versions(self):
        assert c.version_tags(['dev', 'prod', 'latest', '0.5']) == ['0.5']

    def test_large_numbers(self):
        assert c.version_tags(['1.97', '1.100'])[0] == '1.100'


class TestNewestBuilt:

    def test_it_picks_the_newest_ci_built_tag(self):
        tag, rev = c.newest_built('r', lambda r: ['1.1', '1.2', '1.3'],
                                  labelled('1.3'))
        assert (tag, rev) == ('1.3', SHA)

    def test_it_skips_newer_tags_that_ci_did_not_build(self):
        """1.9 exists but has no revision label - someone pushed it by hand.
        Auto-promoting it would put an untraceable image on the fleet."""
        tag, _ = c.newest_built('r', lambda r: ['1.8', '1.9'], labelled('1.8'))
        assert tag == '1.8'

    def test_a_dirty_build_is_not_offered(self):
        tag, reason = c.newest_built(
            'r', lambda r: ['1.9'],
            lambda repo, t: {REV: SHA + '-dirty'})
        assert tag is None

    def test_inherited_base_image_labels_do_not_count(self):
        """Every image carries ref.name/version from its base. Counting those
        would mark every unlabelled image as CI-built."""
        tag, _ = c.newest_built(
            'r', lambda r: ['1.9'],
            lambda repo, t: {'org.opencontainers.image.ref.name': 'ubuntu',
                             'org.opencontainers.image.version': '20.04'})
        assert tag is None

    def test_no_version_tags_at_all(self):
        tag, reason = c.newest_built('r', lambda r: ['dev'], labelled())
        assert tag is None and 'no version-shaped tags' in reason

    def test_a_registry_error_is_reported_not_raised(self):
        """One unreachable repository must not abort the whole survey."""
        def boom(repo):
            raise RuntimeError('HTTP 401 unauthorized')
        tag, reason = c.newest_built('r', boom, labelled())
        assert tag is None and '401' in reason

    def test_it_stops_probing_after_max(self):
        seen = []

        def fetch(repo, tag):
            seen.append(tag)
            return {}
        c.newest_built('r', lambda r: ['1.%d' % i for i in range(50)], fetch,
                       max_probe=3)
        assert len(seen) == 3


class TestSurvey:

    def current(self):
        return {'x86': {'vision': '1.2', 'backend': '1.97'},
                'arm': {'vision': '1.1'}}

    def test_an_upgrade_is_proposed(self):
        records = c.survey(self.current(), lambda r: ['1.2', '1.3'],
                           labelled('1.3'), arches=('x86',))
        vision = [r for r in records if r['component'] == 'vision'][0]
        assert vision['state'] == 'upgrade'
        assert vision['proposed'] == '1.3'

    def test_arches_are_surveyed_independently(self):
        """arm and x86 run different versions of the same component. One
        lookup for both would pin an image that does not exist for an arch."""
        def list_tags(repo):
            return ['1.3'] if 'x86' in repo else ['1.1']
        records = c.survey(self.current(), list_tags, labelled('1.3', '1.1'))
        by = {(r['arch'], r['component']): r['proposed'] for r in records}
        assert by[('x86', 'vision')] == '1.3'
        assert by[('arm', 'vision')] == '1.1'

    def test_it_never_downgrades(self):
        """The newest CI build is older than the pin - perhaps a version tag was
        deleted. Keeping the pin is the only safe answer."""
        records = c.survey({'x86': {'vision': '1.9'}}, lambda r: ['1.2'],
                           labelled('1.2'), arches=('x86',))
        assert records[0]['state'] == 'behind'
        assert records[0]['proposed'] == '1.9'

    def test_a_channel_pin_is_left_alone(self):
        """vernemq is pinned to 'dev', a moving channel. There is no ordering
        to reason about, so a numbered tag is not automatically an improvement."""
        records = c.survey({'x86': {'vernemq': 'dev'}}, lambda r: ['1.0'],
                           labelled('1.0'), arches=('x86',))
        assert records[0]['state'] == 'channel'
        assert records[0]['proposed'] == 'dev'

    def test_nothing_to_do_keeps_the_pin(self):
        records = c.survey({'x86': {'vision': '1.3'}}, lambda r: ['1.3'],
                           labelled('1.3'), arches=('x86',))
        assert records[0]['state'] == 'same'

    def test_an_unbuildable_component_keeps_its_pin(self):
        records = c.survey({'x86': {'vision': '1.2'}}, lambda r: [],
                           labelled(), arches=('x86',))
        assert records[0]['state'] == 'none'
        assert records[0]['proposed'] == '1.2'

    def test_the_order_is_stable(self):
        a = [(r['arch'], r['component']) for r in
             c.survey(self.current(), lambda r: ['1.3'], labelled('1.3'))]
        b = [(r['arch'], r['component']) for r in
             c.survey(self.current(), lambda r: ['1.3'], labelled('1.3'))]
        assert a == b


class TestApply:

    def test_it_does_not_mutate_the_input(self):
        current = {'x86': {'vision': '1.2'}}
        records = c.survey(current, lambda r: ['1.3'], labelled('1.3'),
                           arches=('x86',))
        updated = c.apply(current, records)
        assert current['x86']['vision'] == '1.2'
        assert updated['x86']['vision'] == '1.3'

    def test_untouched_components_are_carried_over(self):
        current = {'x86': {'vision': '1.2', 'backend': '1.97'}}
        records = c.survey(current, lambda r: ['1.3'] if 'vision' in r else [],
                           labelled('1.3'), arches=('x86',))
        updated = c.apply(current, records)
        assert updated['x86']['backend'] == '1.97'


class TestDescribe:

    def test_it_reports_whether_anything_moves(self):
        current = {'x86': {'vision': '1.2'}}
        records = c.survey(current, lambda r: ['1.3'], labelled('1.3'),
                           arches=('x86',))
        out = io.StringIO()
        assert c.describe(records, out) is True
        assert '1.2 -> 1.3' in out.getvalue()

    def test_no_movement_says_so(self):
        records = c.survey({'x86': {'vision': '1.3'}}, lambda r: ['1.3'],
                           labelled('1.3'), arches=('x86',))
        out = io.StringIO()
        assert c.describe(records, out) is False
        assert 'Nothing to move' in out.getvalue()

    def test_a_downgrade_is_surfaced_loudly(self):
        records = c.survey({'x86': {'vision': '1.9'}}, lambda r: ['1.2'],
                           labelled('1.2'), arches=('x86',))
        out = io.StringIO()
        c.describe(records, out)
        assert 'older' in out.getvalue()
