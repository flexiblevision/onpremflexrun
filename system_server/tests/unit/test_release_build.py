"""Cutting a release.

The stamping is all derivation, so these tests are about the derivations being
right and refusing rather than guessing. The counter is the load-bearing one: if
it ever repeats or goes backwards, the device's anti-rollback check is silently
disarmed and a known-bad release becomes replayable.
"""
import datetime
import hashlib
import json
import pytest
from unittest.mock import MagicMock

from release import build_release as b
from release import manifest as m

NOW = datetime.datetime(2026, 8, 26, 23, 0, 0)
COMMIT = 'a' * 40


def resolver(repo, tag):
    return 'sha256:' + hashlib.sha256('{}:{}'.format(repo, tag).encode()).hexdigest()


def all_tags(version='1.9.2'):
    return {c: version for c in m.FOUNDATIONAL}


class TestParseVersionFile:

    @pytest.mark.parametrize('text,expected', [
        ('1.9', (1, 9)), ('1.9\n', (1, 9)), ('  2.0  \n', (2, 0)),
        ('10.24', (10, 24)), ('0.1', (0, 1)),
    ])
    def test_accepts_major_minor(self, text, expected):
        assert b.parse_version_file(text) == expected

    @pytest.mark.parametrize('bad', [
        '1.9.3', 'v1.9', '1', '', None, 'abc', '1.9-rc1', '1,9', 'latest',
    ])
    def test_refuses_anything_else(self, bad):
        """The build number is CI's to own; a patch digit here would collide."""
        with pytest.raises(b.BuildError, match='MAJOR.MINOR'):
            b.parse_version_file(bad)


class TestNextBuild:
    """Monotonic forever. This is the anti-rollback counter."""

    def test_first_release_is_one(self):
        assert b.next_build([]) == 1

    def test_increments_from_the_highest(self):
        assert b.next_build(['release/1', 'release/47']) == 48

    def test_ignores_unrelated_tags(self):
        """A semver tag or a branch-archive tag must not perturb the sequence."""
        assert b.next_build(['v1.9.2', 'archive/v1.8', 'release/12', 'nightly']) == 13

    def test_ignores_malformed_release_tags(self):
        assert b.next_build(['release/x', 'release/', 'release/1.2', 'release/9']) == 10

    def test_uses_the_highest_not_the_count(self):
        """A deleted tag must not cause a number to be reused."""
        assert b.next_build(['release/3', 'release/9', 'release/7']) == 10

    def test_is_not_confused_by_ordering(self):
        assert b.next_build(['release/100', 'release/2']) == 101

    def test_handles_whitespace_from_git_output(self):
        assert b.next_build(['  release/5  ', 'release/6\n']) == 7

    def test_never_returns_a_used_number(self):
        used = ['release/%d' % n for n in range(1, 40)]
        assert b.next_build(used) not in [int(t.split('/')[1]) for t in used]

    def test_survives_none(self):
        assert b.next_build(None) == 1


class TestReleaseVersion:

    def test_joins_the_three_parts(self):
        assert b.release_version(1, 9, 48) == '1.9.48'

    def test_build_number_does_not_reset_on_a_minor_bump(self):
        """1.9.48 -> 1.10.49: the build keeps counting, so the counter stays
        monotonic across a minor bump."""
        assert b.release_version(1, 10, 49) == '1.10.49'


class TestComponentsFromFile:

    def test_reads_a_flat_map(self):
        tags, features = b.components_from_file('{"backend": "6647d89"}')
        assert tags == {'backend': '6647d89'}
        assert features == {}

    def test_reads_components_and_features_sections(self):
        tags, features = b.components_from_file(json.dumps({
            'components': {'backend': '6647d89'},
            'features': {'eventor': '0.4.1'},
        }))
        assert tags == {'backend': '6647d89'}
        assert features == {'eventor': '0.4.1'}

    def test_coerces_values_to_strings(self):
        tags, _ = b.components_from_file('{"backend": 123}')
        assert tags == {'backend': '123'}

    @pytest.mark.parametrize('bad', ['not json', '[]', '{}', 'null', '"x"'])
    def test_refuses_anything_that_is_not_a_component_map(self, bad):
        with pytest.raises(b.BuildError):
            b.components_from_file(bad)

    def test_refuses_a_non_object_features_section(self):
        with pytest.raises(b.BuildError, match='features must be'):
            b.components_from_file('{"components": {"backend": "x"}, "features": []}')


class TestComponentsFromStable:
    """Seeding release 1 from the endpoint the fleet already trusts."""

    def test_asks_for_every_foundational_component(self):
        seen = []

        def fetch(component, arch):
            seen.append(component)
            return '1.9.2'

        tags = b.components_from_stable(fetch)
        assert set(seen) == set(m.FOUNDATIONAL)
        assert set(tags) == set(m.FOUNDATIONAL)

    def test_strips_whitespace_from_the_response_body(self):
        tags = b.components_from_stable(lambda c, a: ' 1.9.2\n')
        assert tags['backend'] == '1.9.2'

    @pytest.mark.parametrize('empty', ['', '   ', None])
    def test_an_unknown_version_is_a_hard_failure(self, empty):
        """An empty response is exactly how the old pipeline silently upgraded
        nothing - it must not become a release."""
        with pytest.raises(b.BuildError, match='returned nothing'):
            b.components_from_stable(lambda c, a: empty)

    def test_names_the_component_that_could_not_be_resolved(self):
        def fetch(component, arch):
            return '' if component == 'vision' else '1.9.2'

        with pytest.raises(b.BuildError, match='vision'):
            b.components_from_stable(fetch)


class TestBuild:

    def test_counter_equals_the_build_number(self):
        """One source of truth, so version and counter cannot disagree."""
        doc, build_no = b.build('1.9', ['release/47'], COMMIT, all_tags(),
                                resolver, NOW)
        assert build_no == 48
        assert doc['release'] == '1.9.48'
        assert doc['counter'] == 48

    def test_first_release_is_counter_one(self):
        doc, build_no = b.build('1.9', [], COMMIT, all_tags(), resolver, NOW)
        assert build_no == 1
        assert doc['counter'] == 1
        assert doc['release'] == '1.9.1'

    def test_pins_the_flexrun_commit(self):
        doc, _ = b.build('1.9', [], COMMIT, all_tags(), resolver, NOW)
        assert doc['flexrun']['commit'] == COMMIT

    def test_carries_features_through(self):
        doc, _ = b.build('1.9', [], COMMIT, all_tags(), resolver, NOW,
                         features={'eventor': '0.4.1'})
        assert 'eventor' in m.features_for(doc, 'x86')

    def test_notes_are_carried(self):
        doc, _ = b.build('1.9', [], COMMIT, all_tags(), resolver, NOW,
                         notes={'summary': 'backend patch',
                                'impact': 'restarts once'})
        assert doc['notes']['summary'] == 'backend patch'
        assert doc['notes']['impact'] == 'restarts once'

    def test_a_bare_string_for_notes_is_refused(self):
        """Schema v2 keeps summary, impact and security separate; accepting a
        string would silently drop impact and security."""
        with pytest.raises(m.ManifestError, match='must be an object'):
            b.build('1.9', [], COMMIT, all_tags(), resolver, NOW,
                    notes='backend patch')

    def test_a_candidate_with_no_notes_is_not_signable(self):
        """CI cuts it; the gate is at signing, not here."""
        doc, _ = b.build('1.9', [], COMMIT, all_tags(), resolver, NOW)
        assert m.notes_shortfall(doc)

    def test_valid_days_sets_notafter(self):
        doc, _ = b.build('1.9', [], COMMIT, all_tags(), resolver, NOW, valid_days=30)
        assert doc['notAfter'] == '2026-09-25T23:00:00Z'

    def test_a_bad_version_file_stops_the_release(self):
        with pytest.raises(b.BuildError):
            b.build('nonsense', [], COMMIT, all_tags(), resolver, NOW)

    def test_a_missing_component_stops_the_release(self):
        partial = all_tags()
        del partial['vision']
        with pytest.raises(m.ManifestError, match='no tag given for'):
            b.build('1.9', [], COMMIT, partial, resolver, NOW)

    def test_consecutive_releases_never_repeat_a_counter(self):
        seen = set()
        tags = []
        for _ in range(5):
            doc, n = b.build('1.9', list(tags), COMMIT, all_tags(), resolver, NOW)
            assert n not in seen
            seen.add(n)
            tags.append('release/%d' % n)
        assert sorted(seen) == [1, 2, 3, 4, 5]


class TestDiffSummary:
    """Release notes are derived from the manifests, not written by hand."""

    def _two(self, second_tags):
        first, _ = b.build('1.9', [], COMMIT, all_tags(), resolver, NOW)
        second, _ = b.build('1.9', ['release/1'], 'b' * 40, second_tags,
                            resolver, NOW)
        return first, second

    def test_a_single_patched_component_is_the_only_change(self):
        tags = all_tags()
        tags['backend'] = '6647d89'
        first, second = self._two(tags)
        summary = b.diff_summary(first, second)
        assert summary['changed'] == ['backend']
        assert len(summary['unchanged']) == len(m.FOUNDATIONAL) - 1

    def test_nothing_changed_reports_nothing_changed(self):
        first, second = self._two(all_tags())
        summary = b.diff_summary(first, second)
        assert summary['changed'] == []
        assert len(summary['unchanged']) == len(m.FOUNDATIONAL)

    def test_all_components_change_when_every_tag_moves(self):
        first, second = self._two(all_tags(version='2.0.0'))
        assert sorted(summary_changed(b.diff_summary(first, second))) == \
            sorted(m.FOUNDATIONAL)

    def test_a_new_feature_is_reported_as_added(self):
        first, _ = b.build('1.9', [], COMMIT, all_tags(), resolver, NOW)
        second, _ = b.build('1.9', ['release/1'], COMMIT, all_tags(), resolver,
                            NOW, features={'eventor': '0.4.1'})
        summary = b.diff_summary(first, second)
        assert summary['added'] == ['eventor']

    def test_a_dropped_feature_is_reported_as_removed(self):
        first, _ = b.build('1.9', [], COMMIT, all_tags(), resolver, NOW,
                           features={'eventor': '0.4.1'})
        second, _ = b.build('1.9', ['release/1'], COMMIT, all_tags(), resolver, NOW)
        summary = b.diff_summary(first, second)
        assert summary['removed'] == ['eventor']

    def test_no_previous_release_reports_everything_as_changed(self):
        first, _ = b.build('1.9', [], COMMIT, all_tags(), resolver, NOW)
        summary = b.diff_summary(None, first)
        assert sorted(summary['changed']) == sorted(m.FOUNDATIONAL)
        assert summary['unchanged'] == []


def summary_changed(summary):
    return summary['changed']


class TestGitHelpers:
    """Injected runner: no repository is touched."""

    def test_release_tags_are_returned_as_a_list(self):
        run = MagicMock(return_value=MagicMock(
            returncode=0, stdout='release/1\nrelease/2\n', stderr=''))
        assert b.git_release_tags(run) == ['release/1', 'release/2']

    def test_no_tags_is_not_an_error(self):
        run = MagicMock(return_value=MagicMock(returncode=0, stdout='', stderr=''))
        assert b.git_release_tags(run) == []

    def test_a_git_failure_is_raised(self):
        run = MagicMock(return_value=MagicMock(
            returncode=128, stdout='', stderr='not a git repository'))
        with pytest.raises(b.BuildError, match='could not list release tags'):
            b.git_release_tags(run)

    def test_head_is_stripped(self):
        run = MagicMock(return_value=MagicMock(
            returncode=0, stdout=COMMIT + '\n', stderr=''))
        assert b.git_head(run) == COMMIT

    def test_a_head_failure_is_raised(self):
        run = MagicMock(return_value=MagicMock(
            returncode=128, stdout='', stderr='fatal'))
        with pytest.raises(b.BuildError, match='could not read HEAD'):
            b.git_head(run)


class TestCli:

    def test_requires_a_component_source(self):
        with pytest.raises(SystemExit):
            b.main(['--version-file', 'release/VERSION'])

    def test_refuses_both_component_sources(self):
        """Two sources of truth for the component set is not a release."""
        with pytest.raises(SystemExit):
            b.main(['--components', 'x.json', '--from-stable'])
