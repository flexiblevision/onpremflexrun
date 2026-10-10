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
    """"<major> <first-counter>": the major series, and the counter whose
    release is .0 in it. Two numbers because the counter must stay monotonic
    for anti-rollback and cannot restart at 0 for a new series."""

    @pytest.mark.parametrize('text,expected', [
        ('1 1', (1, 1)), ('1 4\n', (1, 4)), ('  2 14  \n', (2, 14)),
        ('10 250', (10, 250)), ('0 1', (0, 1)),
    ])
    def test_accepts_major_and_first_counter(self, text, expected):
        assert b.parse_version_file(text) == expected

    @pytest.mark.parametrize('bad', [
        '1.9', '1.9.3', 'v1.9', '1', '', None, 'abc', '1,9', 'latest', '1 4 5',
    ])
    def test_refuses_anything_else(self, bad):
        """'1.9' is refused deliberately - it was the previous valid form, and
        reading its 9 as a first-counter would derive a minor from a number
        that meant something entirely different."""
        with pytest.raises(b.BuildError, match='first-counter'):
            b.parse_version_file(bad)


class TestNextBuild:
    """Monotonic forever. This is the anti-rollback counter."""

    def test_first_release_is_one(self):
        assert b.next_build([], 'x86') == 1

    def test_increments_from_the_highest(self):
        assert b.next_build(['release/x86/1', 'release/x86/47'], 'x86') == 48

    def test_ignores_unrelated_tags(self):
        """A semver tag or a branch-archive tag must not perturb the sequence."""
        assert b.next_build(['v1.9.2', 'archive/v1.8', 'release/x86/12', 'nightly'], 'x86') == 13

    def test_ignores_malformed_release_tags(self):
        assert b.next_build(['release/x', 'release/', 'release/1.2', 'release/x86/9'], 'x86') == 10

    def test_uses_the_highest_not_the_count(self):
        """A deleted tag must not cause a number to be reused."""
        assert b.next_build(['release/x86/3', 'release/x86/9', 'release/x86/7'], 'x86') == 10

    def test_is_not_confused_by_ordering(self):
        assert b.next_build(['release/x86/100', 'release/x86/2'], 'x86') == 101

    def test_handles_whitespace_from_git_output(self):
        assert b.next_build(['  release/x86/5  ', 'release/x86/6\n'], 'x86') == 7

    def test_never_returns_a_used_number(self):
        used = ['release/x86/%d' % n for n in range(1, 40)]
        assert b.next_build(used, 'x86') not in [int(t.split('/')[2]) for t in used]

    def test_survives_none(self):
        assert b.next_build(None, 'x86') == 1


class TestReleaseVersion:
    """MAJOR.MINOR, minor derived from the counter so it cannot be forgotten
    or reused. Two digits, not three: the counter is what a device compares,
    and the version string is for people."""

    def test_the_first_counter_of_a_series_is_dot_zero(self):
        assert b.release_version(1, 4, 4) == '1.0'

    def test_the_minor_increments_once_per_release(self):
        assert [b.release_version(1, 4, c) for c in (4, 5, 6)] == \
            ['1.0', '1.1', '1.2']

    def test_double_digit_minors_are_fine(self):
        """Beta and stable share one counter, so a series has to survive
        several beta iterations per stable release."""
        assert b.release_version(1, 4, 14) == '1.10'

    def test_past_the_ceiling_it_refuses_and_says_what_to_write(self):
        """The next release is a new major series - a decision rather than a
        carry - and the error names the exact line to write."""
        with pytest.raises(b.BuildError, match='exhausted'):
            b.release_version(1, 4, 104)
        try:
            b.release_version(1, 4, 104)
        except b.BuildError as exc:
            assert '"2 104"' in str(exc)

    def test_a_new_series_starts_at_zero_without_resetting_the_counter(self):
        """The counter keeps climbing across a major bump - it has to, or a
        device would refuse the new release as a rollback."""
        assert b.release_version(2, 14, 14) == '2.0'
        assert b.release_version(2, 14, 15) == '2.1'

    def test_a_counter_below_the_series_start_is_refused(self):
        with pytest.raises(b.BuildError, match='below the first counter'):
            b.release_version(2, 14, 13)


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

    def test_the_counter_and_the_version_come_from_one_source(self):
        """The minor is derived from the counter, so they cannot disagree -
        counter 48 in the series starting at 44 is 1.4."""
        doc, build_no = b.build('1 44', ['release/x86/47'], COMMIT, all_tags(),
                                resolver, NOW)
        assert build_no == 48
        assert doc['release'] == '1.4'
        assert doc['counter'] == 48

    def test_first_release_is_counter_one(self):
        doc, build_no = b.build('1 1', [], COMMIT, all_tags(), resolver, NOW)
        assert build_no == 1
        assert doc['counter'] == 1
        assert doc['release'] == '1.0'

    def test_pins_the_flexrun_commit(self):
        doc, _ = b.build('1 1', [], COMMIT, all_tags(), resolver, NOW)
        assert doc['flexrun']['commit'] == COMMIT

    def test_carries_features_through(self):
        doc, _ = b.build('1 1', [], COMMIT, all_tags(), resolver, NOW,
                         features={'eventor': '0.4.1'})
        assert 'eventor' in m.features_for(doc, 'x86')

    def test_notes_are_carried(self):
        doc, _ = b.build('1 1', [], COMMIT, all_tags(), resolver, NOW,
                         notes={'summary': 'backend patch',
                                'impact': 'restarts once'})
        assert doc['notes']['summary'] == 'backend patch'
        assert doc['notes']['impact'] == 'restarts once'

    def test_a_bare_string_for_notes_is_refused(self):
        """Schema v2 keeps summary, impact and security separate; accepting a
        string would silently drop impact and security."""
        with pytest.raises(m.ManifestError, match='must be an object'):
            b.build('1 1', [], COMMIT, all_tags(), resolver, NOW,
                    notes='backend patch')

    def test_a_candidate_with_no_notes_is_not_signable(self):
        """CI cuts it; the gate is at signing, not here."""
        doc, _ = b.build('1 1', [], COMMIT, all_tags(), resolver, NOW)
        assert m.notes_shortfall(doc)

    def test_valid_days_sets_notafter(self):
        doc, _ = b.build('1 1', [], COMMIT, all_tags(), resolver, NOW, valid_days=30)
        assert doc['notAfter'] == '2026-09-25T23:00:00Z'

    def test_a_bad_version_file_stops_the_release(self):
        with pytest.raises(b.BuildError):
            b.build('nonsense', [], COMMIT, all_tags(), resolver, NOW)

    def test_a_missing_component_stops_the_release(self):
        partial = all_tags()
        del partial['vision']
        with pytest.raises(m.ManifestError, match='no tag given for'):
            b.build('1 1', [], COMMIT, partial, resolver, NOW)

    def test_consecutive_releases_never_repeat_a_counter(self):
        seen = set()
        tags = []
        for _ in range(5):
            doc, n = b.build('1 1', list(tags), COMMIT, all_tags(), resolver, NOW)
            assert n not in seen
            seen.add(n)
            tags.append('release/x86/%d' % n)
        assert sorted(seen) == [1, 2, 3, 4, 5]


class TestDiffSummary:
    """Release notes are derived from the manifests, not written by hand."""

    def _two(self, second_tags):
        first, _ = b.build('1 1', [], COMMIT, all_tags(), resolver, NOW)
        second, _ = b.build('1 1', ['release/x86/1'], 'b' * 40, second_tags,
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
        first, _ = b.build('1 1', [], COMMIT, all_tags(), resolver, NOW)
        second, _ = b.build('1 1', ['release/x86/1'], COMMIT, all_tags(), resolver,
                            NOW, features={'eventor': '0.4.1'})
        summary = b.diff_summary(first, second)
        assert summary['added'] == ['eventor']

    def test_a_dropped_feature_is_reported_as_removed(self):
        first, _ = b.build('1 1', [], COMMIT, all_tags(), resolver, NOW,
                           features={'eventor': '0.4.1'})
        second, _ = b.build('1 1', ['release/x86/1'], COMMIT, all_tags(), resolver, NOW)
        summary = b.diff_summary(first, second)
        assert summary['removed'] == ['eventor']

    def test_no_previous_release_reports_everything_as_changed(self):
        first, _ = b.build('1 1', [], COMMIT, all_tags(), resolver, NOW)
        summary = b.diff_summary(None, first)
        assert sorted(summary['changed']) == sorted(m.FOUNDATIONAL)
        assert summary['unchanged'] == []


def summary_changed(summary):
    return summary['changed']


class TestGitHelpers:
    """Injected runner: no repository is touched."""

    def test_release_tags_are_returned_as_a_list(self):
        run = MagicMock(return_value=MagicMock(
            returncode=0, stdout='release/x86/1\nrelease/x86/2\n', stderr=''))
        assert b.git_release_tags(run) == ['release/x86/1', 'release/x86/2']

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


class TestPerArchCounters:
    """x86 and arm ship on their own cadence. A shared sequence would make an
    arm device look behind because an x86 release consumed a number it will
    never see."""

    def test_each_arch_counts_independently(self):
        tags = ['release/x86/1', 'release/x86/2', 'release/arm/1']
        assert b.next_build(tags, 'x86') == 3
        assert b.next_build(tags, 'arm') == 2

    def test_another_arch_cannot_move_this_one(self):
        assert b.next_build(['release/arm/99'], 'x86') == 1

    def test_an_unknown_arch_starts_at_one(self):
        assert b.next_build(['release/x86/7'], 'riscv') == 1

    def test_the_old_flat_tag_format_is_ignored(self):
        """release/<n> predates per-arch counters. Counting it would restart
        x86 from a number that is not its own."""
        assert b.next_build(['release/12'], 'x86') == 1

    def test_an_arch_is_required(self):
        with pytest.raises(b.BuildError, match='per architecture'):
            b.next_build(['release/x86/1'], None)

    def test_reserve_names_the_arch_in_the_tag(self):
        calls = []

        def run(argv):
            calls.append(argv)
            return MagicMock(returncode=0, stdout='', stderr='')

        assert b.reserve_build(3, 'c' * 40, 'arm', run) == 'refs/tags/release/arm/3'
        assert calls[-1] == ['git', 'push', 'origin', 'refs/tags/release/arm/3']


class TestMajorJump:
    """Starting a new series, e.g. calling the next release 2.0.

    Decided at the cut, not at promote: the version string is inside the signed
    bytes, so renaming at promote time means re-signing - and then stable would
    not be shipping the manifest beta tested."""

    def test_it_starts_the_new_series_at_zero(self):
        doc, n = b.build('1 4', ['release/x86/9'], COMMIT, all_tags(),
                         resolver, NOW, major_override=2)
        assert doc['release'] == '2.0'
        assert n == 10

    def test_the_counter_keeps_climbing_across_the_jump(self):
        """It has to. A counter that restarted would be refused by every device
        that had already passed it."""
        doc, n = b.build('1 4', ['release/x86/9'], COMMIT, all_tags(),
                         resolver, NOW, major_override=2)
        assert doc['counter'] == 10 > 9

    def test_without_the_override_the_series_continues(self):
        doc, _ = b.build('1 4', ['release/x86/9'], COMMIT, all_tags(),
                         resolver, NOW)
        assert doc['release'] == '1.6'

    def test_jumping_backwards_is_refused(self):
        """'2 -> 1' would produce release strings that go backwards while the
        counter goes forwards, which is the most confusing possible pair."""
        with pytest.raises(b.BuildError, match='goes forwards'):
            b.build('2 4', [], COMMIT, all_tags(), resolver, NOW,
                    major_override=1)

    def test_jumping_to_the_same_major_is_refused(self):
        with pytest.raises(b.BuildError, match='not ahead'):
            b.build('2 4', [], COMMIT, all_tags(), resolver, NOW,
                    major_override=2)

    def test_a_later_release_continues_the_new_series(self):
        """After the jump, VERSION is rewritten to "2 <counter>", so the next
        release is 2.1 rather than falling back to 1.x."""
        doc, _ = b.build('2 10', ['release/x86/10'], COMMIT, all_tags(),
                         resolver, NOW)
        assert doc['release'] == '2.1'


class TestMinorCeiling:

    def test_the_series_runs_to_ninety_nine(self):
        assert b.MAX_MINOR == 99
        assert b.release_version(1, 4, 103) == '1.99'

    def test_past_the_ceiling_it_asks_for_a_new_series(self):
        with pytest.raises(b.BuildError, match='exhausted'):
            b.release_version(1, 4, 104)
