"""The notes gate: it must not be satisfiable by accident, and the derived half
must agree with the manifests it came from."""
import datetime
import hashlib
import json
import pytest
from unittest.mock import MagicMock

from release import build_release as b
from release import manifest as m
from release import prepare as p

NOW = datetime.datetime(2026, 8, 26, 23, 0, 0)
COMMIT = 'a' * 40


def resolver(repo, tag):
    return 'sha256:' + hashlib.sha256('{}:{}'.format(repo, tag).encode()).hexdigest()


def all_tags(version='1.9.2'):
    return {c: version for c in m.FOUNDATIONAL}


def cut(existing_tags=(), tags=None, features=None, commit=COMMIT):
    document, _ = b.build('1.9', list(existing_tags), commit,
                          tags or all_tags(), resolver, NOW, features=features)
    return document


def filled(summary='mqtt listener no longer drops on reconnect',
           impact='capdev restarts once, about 20 seconds',
           security='no', reference=None):
    """An editor that returns a properly completed template."""
    def editor(_template):
        text = 'security: {}\n\nsummary:\n{}\n\nimpact:\n{}\n'.format(
            security, summary, impact)
        if reference is not None:
            text += '\nreference:\n{}\n'.format(reference)
        return text
    return editor


class TestDeriveNotes:
    """The factual half, from the manifests."""

    def test_no_previous_release_lists_every_component_as_new(self):
        derived = p.derive_notes(None, cut())
        assert len(derived['changed']) == len(m.FOUNDATIONAL)
        assert all(entry['from'] is None for entry in derived['changed'])
        assert derived['unchanged'] == 0

    def test_a_single_patched_component_is_the_only_change(self):
        first = cut()
        tags = all_tags()
        tags['backend'] = '6647d89'
        second = cut(['release/1'], tags)

        derived = p.derive_notes(first, second)
        assert [e['component'] for e in derived['changed']] == ['backend']
        assert derived['unchanged'] == len(m.FOUNDATIONAL) - 1

    def test_a_change_records_both_tags(self):
        first = cut()
        tags = all_tags()
        tags['backend'] = '6647d89'
        entry = p.derive_notes(first, cut(['release/1'], tags))['changed'][0]
        assert entry['from'] == '1.9.2'
        assert entry['to'] == '6647d89'

    def test_nothing_moving_reports_nothing_changed(self):
        first = cut()
        derived = p.derive_notes(first, cut(['release/1']))
        assert derived['changed'] == []
        assert derived['unchanged'] == len(m.FOUNDATIONAL)

    def test_a_retag_at_the_same_name_is_still_a_change(self):
        """The digest is the truth, not the tag."""
        first = cut()
        second = json.loads(json.dumps(first))
        component = m.components_for(second, 'x86')['backend']
        component['digest'] = 'sha256:' + 'b' * 64

        derived = p.derive_notes(first, second)
        assert [e['component'] for e in derived['changed']] == ['backend']
        assert derived['changed'][0]['from'] == derived['changed'][0]['to']

    def test_a_new_feature_is_reported_as_added(self):
        first = cut()
        second = cut(['release/1'], features={'eventor': '0.4.1'})
        assert p.derive_notes(first, second)['features_added'] == ['eventor']

    def test_a_dropped_feature_is_reported_as_removed(self):
        first = cut(features={'eventor': '0.4.1'})
        second = cut(['release/1'])
        assert p.derive_notes(first, second)['features_removed'] == ['eventor']

    def test_changed_components_are_ordered_stably(self):
        first = cut()
        tags = all_tags()
        tags['vision'] = 'x'
        tags['backend'] = 'y'
        names = [e['component'] for e in
                 p.derive_notes(first, cut(['release/1'], tags))['changed']]
        assert names == sorted(names)


class TestRenderTemplate:

    def _template(self, **kwargs):
        current = kwargs.pop('current', None) or cut()
        derived = kwargs.pop('derived', None) or p.derive_notes(None, current)
        return p.render_template(current, derived, **kwargs)

    def test_names_the_release_and_counter(self):
        text = self._template()
        assert '1.9.1' in text
        assert 'counter 1' in text

    def test_shows_what_changed_for_reference(self):
        first = cut()
        tags = all_tags()
        tags['backend'] = '6647d89'
        second = cut(['release/1'], tags)
        text = self._template(current=second,
                              derived=p.derive_notes(first, second))
        assert 'backend' in text
        assert '1.9.2 -> 6647d89' in text

    def test_a_new_component_shows_as_new(self):
        assert '(new)' in self._template()

    def test_nothing_changed_says_so(self):
        first = cut()
        second = cut(['release/1'])
        text = self._template(current=second,
                             derived=p.derive_notes(first, second))
        assert 'changed:   nothing' in text

    def test_warns_that_the_notes_are_public(self):
        """Signed and shipped: anything written here is published."""
        text = self._template().lower()
        assert 'visible to anyone' in text
        assert 'credentials' in text

    def test_commit_subjects_appear_only_as_comments(self):
        text = self._template(reference='fix mqtt setup script\nbump pillow')
        for line in text.splitlines():
            if 'fix mqtt setup script' in line:
                assert line.lstrip().startswith('#')

    def test_commit_subjects_are_truncated(self):
        text = self._template(reference='\n'.join(
            'commit %d' % n for n in range(200)))
        assert 'commit 39' in text
        assert 'commit 40' not in text

    def test_offers_a_reference_block_to_fill(self):
        assert 'reference:' in self._template()


class TestParseNotes:

    def test_reads_the_three_human_fields(self):
        parsed = p.parse_notes(
            'security: yes\n\nsummary:\nwhy\n\nimpact:\nwhat\n')
        assert parsed['summary'] == 'why'
        assert parsed['impact'] == 'what'
        assert parsed['security'] is True

    def test_comments_are_dropped(self):
        parsed = p.parse_notes(
            '# Release 1.9.1\n#   changed: backend\nsummary:\nreal text\n')
        assert parsed['summary'] == 'real text'

    def test_a_multiline_block_is_kept_whole(self):
        parsed = p.parse_notes('summary:\nline one\nline two\n')
        assert parsed['summary'] == 'line one\nline two'

    @pytest.mark.parametrize('value,expected', [
        ('yes', True), ('no', False), ('true', True), ('false', False),
        ('YES', True), ('No', False), ('', False),
    ])
    def test_security_accepts_yes_or_no_in_any_case(self, value, expected):
        parsed = p.parse_notes('security: {}\nsummary:\nx\n'.format(value))
        assert parsed['security'] is expected

    def test_an_ambiguous_security_value_is_refused(self):
        """Reading 'maybe' as no would understate a security release."""
        with pytest.raises(p.PrepareError, match='security must be yes or no'):
            p.parse_notes('security: maybe\nsummary:\nx\n')

    def test_prose_starting_with_security_fails_loudly(self):
        """Known wart: a summary line starting 'security:' is read as the flag.
        It raises rather than swallowing the line."""
        with pytest.raises(p.PrepareError):
            p.parse_notes('summary:\nsecurity: hardened the listener\n')

    def test_a_placeholder_counts_as_empty(self):
        parsed = p.parse_notes(
            'summary:\n<one sentence: why this release matters>\n')
        assert parsed['summary'] == ''

    def test_an_empty_reference_is_left_out_entirely(self):
        assert 'reference' not in p.parse_notes('summary:\nx\n\nreference:\n')

    def test_a_filled_reference_is_kept(self):
        parsed = p.parse_notes('summary:\nx\n\nreference:\nsee INC-4471\n')
        assert parsed['reference'] == 'see INC-4471'

    def test_an_empty_document_yields_empty_fields(self):
        parsed = p.parse_notes('')
        assert parsed['summary'] == ''
        assert parsed['impact'] == ''
        assert parsed['security'] is False

    def test_survives_none(self):
        assert p.parse_notes(None)['summary'] == ''

    def test_an_unedited_template_cannot_satisfy_the_gate(self):
        """Load-bearing: saving the template unchanged must not pass the gate.

        impact arrives pre-filled from the diff, so summary is what holds the
        gate shut - which is why exactly one field is left blank."""
        current = cut()
        template = p.render_template(current, p.derive_notes(None, current))
        parsed = p.parse_notes(template)
        assert parsed['summary'] == ''
        assert parsed['impact'], 'impact should be generated, not blank'


class TestPrepare:

    def test_completed_notes_produce_signable_bytes(self):
        raw = p.prepare(json.dumps(cut()), editor=filled())
        document = json.loads(raw.decode('utf-8'))
        assert document['notes']['summary'].startswith('mqtt listener')
        assert document['notes']['impact'].startswith('capdev restarts')
        assert not m.notes_shortfall(document)

    def test_an_unedited_template_is_refused(self):
        with pytest.raises(p.PrepareError, match='notes are incomplete'):
            p.prepare(json.dumps(cut()), editor=lambda template: template)

    def test_an_editor_that_saves_nothing_is_refused(self):
        with pytest.raises(p.PrepareError, match='notes are incomplete'):
            p.prepare(json.dumps(cut()), editor=lambda _: '')

    def test_a_missing_summary_is_refused(self):
        with pytest.raises(p.PrepareError, match='summary'):
            p.prepare(json.dumps(cut()), editor=filled(summary=''))

    def test_a_missing_impact_is_refused_when_something_changed(self):
        with pytest.raises(p.PrepareError, match='impact'):
            p.prepare(json.dumps(cut()), editor=filled(impact=''))

    def test_impact_is_not_demanded_when_nothing_changed(self):
        """Demanding prose about nothing teaches signers to write filler."""
        first = cut()
        raw = p.prepare(json.dumps(cut(['release/1'])), json.dumps(first),
                        editor=filled(impact=''))
        assert json.loads(raw.decode('utf-8'))['notes']['impact'] == ''

    def test_the_derived_half_survives_the_editor(self):
        """The facts come from the manifests, not the text."""
        first = cut()
        tags = all_tags()
        tags['backend'] = '6647d89'
        raw = p.prepare(json.dumps(cut(['release/1'], tags)),
                        json.dumps(first), editor=filled())
        notes = json.loads(raw.decode('utf-8'))['notes']
        assert [e['component'] for e in notes['changed']] == ['backend']
        assert notes['unchanged'] == len(m.FOUNDATIONAL) - 1

    def test_a_security_release_is_marked(self):
        raw = p.prepare(json.dumps(cut()), editor=filled(security='yes'))
        assert json.loads(raw.decode('utf-8'))['notes']['security'] is True

    def test_commit_subjects_are_discarded_unless_copied(self):
        raw = p.prepare(json.dumps(cut()), editor=filled(),
                        reference='fix mqtt setup script')
        document = json.loads(raw.decode('utf-8'))
        assert 'fix mqtt setup script' not in raw.decode('utf-8')
        assert 'reference' not in document['notes']

    def test_a_copied_reference_is_kept(self):
        raw = p.prepare(json.dumps(cut()),
                        editor=filled(reference='see INC-4471'))
        assert json.loads(raw.decode('utf-8'))['notes']['reference'] == \
            'see INC-4471'

    def test_the_output_is_canonical(self):
        """Signing is over these exact bytes."""
        candidate = json.dumps(cut())
        first = p.prepare(candidate, editor=filled())
        second = p.prepare(candidate, editor=filled())
        assert first == second
        assert first == m.canonical_bytes(json.loads(first.decode('utf-8')))

    def test_nothing_but_the_notes_is_touched(self):
        candidate = cut()
        raw = p.prepare(json.dumps(candidate), editor=filled())
        prepared = json.loads(raw.decode('utf-8'))
        for field in ('release', 'counter', 'schema', 'images', 'flexrun'):
            assert prepared[field] == candidate[field]

    def test_a_structurally_invalid_candidate_is_refused(self):
        broken = cut()
        del broken['counter']
        with pytest.raises((p.PrepareError, m.ManifestError)):
            p.prepare(json.dumps(broken), editor=filled())

    def test_a_candidate_that_is_not_json_is_refused(self):
        with pytest.raises((p.PrepareError, m.ManifestError)):
            p.prepare('not json at all', editor=filled())

    def test_the_editor_is_shown_the_derived_facts(self):
        seen = {}

        def editor(template):
            seen['template'] = template
            return filled()(template)

        first = cut()
        tags = all_tags()
        tags['backend'] = '6647d89'
        p.prepare(json.dumps(cut(['release/1'], tags)), json.dumps(first),
                  editor=editor)
        assert 'backend' in seen['template']


class TestCommitReference:
    """Reference material only, so a git failure must not stop a release."""

    def test_returns_the_subjects(self):
        run = MagicMock(return_value=MagicMock(
            returncode=0, stdout='fix mqtt setup\nbump pillow\n', stderr=''))
        assert p.commit_reference('release/1', run) == \
            'fix mqtt setup\nbump pillow'

    def test_a_git_failure_is_not_fatal(self):
        run = MagicMock(return_value=MagicMock(
            returncode=128, stdout='', stderr='fatal: bad revision'))
        assert p.commit_reference('release/1', run) == ''

    def test_no_tag_reads_the_whole_history(self):
        run = MagicMock(return_value=MagicMock(returncode=0, stdout='', stderr=''))
        p.commit_reference(None, run)
        assert run.call_args[0][0][-1] == 'HEAD'

    def test_a_tag_bounds_the_span(self):
        run = MagicMock(return_value=MagicMock(returncode=0, stdout='', stderr=''))
        p.commit_reference('release/7', run)
        assert run.call_args[0][0][-1] == 'release/7..HEAD'

    def test_merge_commits_are_excluded(self):
        run = MagicMock(return_value=MagicMock(returncode=0, stdout='', stderr=''))
        p.commit_reference('release/7', run)
        assert '--no-merges' in run.call_args[0][0]


class TestDeriveImpact:
    """The generated half of the notes. Read by an operator deciding whether to
    interrupt a running line, so it names what they lose, not container names
    alone - and never asserts a duration nobody measured."""

    def _derived(self, *components):
        return {'changed': [{'component': c, 'from': 'a', 'to': 'b'}
                            for c in components],
                'unchanged': 0, 'features_added': [], 'features_removed': []}

    def test_nothing_changed_says_nothing_restarts(self):
        assert 'nothing restarts' in p.derive_impact(self._derived())

    def test_it_names_the_containers_that_restart(self):
        text = p.derive_impact(self._derived('backend', 'vision'))
        assert 'backend, vision' in text

    def test_it_counts_them(self):
        assert '2 containers restart' in p.derive_impact(
            self._derived('backend', 'vision'))

    def test_one_container_reads_as_singular(self):
        text = p.derive_impact(self._derived('backend'))
        assert '1 container restarts' in text
        assert 'containers' not in text

    def test_it_says_what_the_operator_loses(self):
        text = p.derive_impact(self._derived('backend'))
        assert 'image capture is unavailable' in text

    def test_effects_are_not_repeated(self):
        """prediction and predictlite both pause inference; saying it twice
        reads like two separate outages."""
        text = p.derive_impact(self._derived('prediction', 'predictlite'))
        assert text.count('inference pauses') == 1

    def test_an_unmapped_component_still_reports_the_restart(self):
        text = p.derive_impact(self._derived('somethingnew'))
        assert 'somethingnew' in text

    def test_it_never_claims_a_duration(self):
        """No restart time has been measured on real hardware, and a confident
        wrong number is worse than none for someone deciding to interrupt
        production."""
        text = p.derive_impact(self._derived('backend', 'vision', 'frontend'))
        assert 'second' not in text and 'minute' not in text
        assert not any(ch.isdigit() for ch in text.split(':')[0].replace('3', ''))

    def test_it_is_ordered_so_two_runs_read_the_same(self):
        one = p.derive_impact(self._derived('vision', 'backend'))
        two = p.derive_impact(self._derived('backend', 'vision'))
        assert one == two


class TestTemplatePreFillsImpact:

    def _template(self, *components):
        current = cut()
        derived = {'changed': [{'component': c, 'from': 'a', 'to': 'b'}
                               for c in components],
                   'unchanged': 0, 'features_added': [], 'features_removed': []}
        return p.render_template(current, derived)

    def test_impact_arrives_filled_in(self):
        assert 'image capture is unavailable' in self._template('backend')

    def test_summary_is_still_a_placeholder(self):
        """If both were pre-filled the notes gate would stop biting - an
        unedited template would sail through."""
        assert '<one sentence' in self._template('backend')

    def test_an_unedited_template_still_fails_the_gate(self):
        template = self._template('backend')
        parsed = p.parse_notes(template)
        assert parsed['impact']
        assert parsed['summary'] == ''

    def test_prepare_still_refuses_without_a_summary(self):
        with pytest.raises(p.PrepareError, match='summary'):
            p.prepare(json.dumps(cut()), editor=lambda t: t)
