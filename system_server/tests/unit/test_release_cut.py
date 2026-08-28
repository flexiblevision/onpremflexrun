"""The guided cut.

Most of the value is in preflight, so most of these tests are about it failing
before any work happens. Getting through digest resolution and notes and then
discovering cosign is missing is the specific experience this exists to prevent.
"""
import base64
import datetime
import hashlib
import json
import os
import pytest

from release import build_release as build_mod
from release import cut as c
from release import manifest as m
from release import prepare as prepare_mod
from release import sign as sign_mod

NOW = datetime.datetime(2026, 8, 27, 12, 0, 0)
HEAD = 'a' * 40


def FAKE_RESERVE(build_no, commit, message=None):
    """Stand-in for the tag push. Every cut() call in these tests must pass a
    reserve, or it would push a real git tag to the real remote."""
    return 'refs/tags/release/{}'.format(build_no)


def resolver(repo, tag):
    return 'sha256:' + hashlib.sha256('{}:{}'.format(repo, tag).encode()).hexdigest()


def git_stub(dirty=False, head=HEAD, fail=False):
    class Result:
        def __init__(self, returncode, stdout):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ''

    def run(argv):
        if 'rev-parse' in argv:
            return Result(128 if fail else 0, '' if fail else head + '\n')
        if 'status' in argv:
            return Result(0, ' M a.py\n M b.py\n' if dirty else '')
        return Result(0, '')
    return run


def env(user='fv', token='tok'):
    out = {}
    if user:
        out['DOCKERHUB_USERNAME'] = user
    if token:
        out['DOCKERHUB_TOKEN'] = token
    return out


def checks(**kwargs):
    defaults = dict(key_path=__file__, version_text='1.9',
                    which=lambda n: '/usr/local/bin/' + n,
                    environ=env(), run=git_stub())
    defaults.update(kwargs)
    return c.preflight(**defaults)


def named(result, label):
    return [chk for chk in result if chk.label == label][0]


def cosign_key(tmp_path):
    """A key signer_for maps to the cosign delegate, so cosign is required."""
    key = tmp_path / 'cosign.key'
    key.write_text('-----BEGIN ENCRYPTED SIGSTORE PRIVATE KEY-----\nx\n')
    return str(key)


KMS_REF = ('gcpkms://projects/p/locations/us-central1/keyRings/r/'
           'cryptoKeys/k/versions/1')


class TestPreflight:

    def test_a_ready_environment_passes_everything(self):
        assert all(chk.ok for chk in checks())

    def test_missing_cosign_is_fatal_when_cosign_is_the_signer(self, tmp_path):
        chk = named(checks(key_path=cosign_key(tmp_path), which=lambda n: None),
                    'cosign installed')
        assert not chk.ok and chk.fatal
        assert 'sigstore/cosign' in chk.fix

    def test_cosign_is_not_required_for_a_kms_key(self):
        """Nothing shells out to cosign for a KMS or openssl key, and devices
        never do - so demanding a 141MB binary would be noise."""
        chk = named(checks(key_path=KMS_REF, which=lambda n: None),
                    'cosign installed')
        assert chk.ok or not chk.fatal

    def test_a_kms_reference_is_parsed_and_shown(self):
        chk = named(checks(key_path=KMS_REF), 'KMS key reference')
        assert chk.ok
        assert 'r/k v1' in chk.detail

    def test_a_malformed_kms_reference_is_fatal(self):
        chk = named(checks(key_path='gcpkms://projects/p'), 'KMS key reference')
        assert not chk.ok and chk.fatal

    def test_a_kms_key_needs_gcloud(self):
        chk = named(checks(key_path=KMS_REF, which=lambda n: None),
                    'gcloud installed')
        assert not chk.ok and chk.fatal

    def test_a_kms_key_is_not_checked_for_being_a_file(self):
        labels = [chk.label for chk in checks(key_path=KMS_REF)]
        assert 'signing key readable' not in labels

    def test_a_missing_key_is_fatal(self):
        assert not named(checks(key_path='/nope/cosign.key'),
                         'signing key readable').ok

    def test_no_key_given_at_all_is_fatal(self):
        assert not named(checks(key_path=None), 'signing key readable').ok

    @pytest.mark.parametrize('user,token', [(None, 'tok'), ('fv', None), (None, None)])
    def test_partial_docker_credentials_are_refused(self, user, token):
        """Half-set credentials fail at the registry with a bare HTTP 401."""
        chk = named(checks(environ=env(user, token)), 'Docker Hub credentials')
        assert not chk.ok
        assert 'private' in chk.fix

    def test_credentials_can_be_downgraded_to_a_warning(self):
        chk = named(checks(environ={}, need_credentials=False),
                    'Docker Hub credentials')
        assert not chk.ok and not chk.fatal

    @pytest.mark.parametrize('bad', ['1.9.3', 'v1.9', '', 'latest'])
    def test_a_bad_version_file_is_fatal(self, bad):
        chk = named(checks(version_text=bad), 'release/VERSION')
        assert not chk.ok
        assert 'MAJOR.MINOR' in chk.fix

    def test_the_version_is_echoed_back(self):
        assert named(checks(version_text='2.0\n'), 'release/VERSION').detail == '2.0'

    def test_an_unresolvable_head_is_fatal(self):
        assert not named(checks(run=git_stub(fail=True)), 'git HEAD').ok

    def test_a_dirty_tree_is_fatal_by_default(self):
        """The manifest pins HEAD, so a dirty tree means the pinned commit is
        not what was tested."""
        chk = named(checks(run=git_stub(dirty=True)), 'working tree clean')
        assert not chk.ok
        assert '2 modified' in chk.detail

    def test_allow_dirty_overrides_it(self):
        assert named(checks(run=git_stub(dirty=True), allow_dirty=True),
                     'working tree clean').ok

    def test_a_clean_tree_says_clean(self):
        assert named(checks(), 'working tree clean').detail == 'clean'


class TestRenderChecks:

    class Stream:
        def __init__(self):
            self.text = ''

        def write(self, chunk):
            self.text += chunk

    def test_blocking_checks_are_returned(self, tmp_path):
        stream = self.Stream()
        blocking = c.render_checks(
            checks(key_path=cosign_key(tmp_path), which=lambda n: None), stream)
        assert [chk.label for chk in blocking] == ['cosign installed']

    def test_a_warning_does_not_block(self):
        stream = self.Stream()
        blocking = c.render_checks(
            checks(environ={}, need_credentials=False), stream)
        assert blocking == []
        assert 'warn' in stream.text

    def test_the_fix_is_printed_for_a_failure(self, tmp_path):
        stream = self.Stream()
        c.render_checks(
            checks(key_path=cosign_key(tmp_path), which=lambda n: None), stream)
        assert 'sigstore/cosign' in stream.text


class TestTagsFromStable:

    def test_it_skips_what_an_arch_does_not_have(self):
        """visiontools has no arm image, so asking for one would 500 and a
        release must not pin it."""
        tags, unresolved = c.tags_from_stable(
            lambda comp, arch: '1.0', overrides={'vernemq': 'prod'})
        assert 'visiontools' in tags['x86']
        assert 'visiontools' not in tags['arm']
        assert unresolved == []

    def test_an_override_wins_without_calling_the_endpoint(self):
        asked = []

        def fetch(comp, arch):
            asked.append(comp)
            return '1.0'

        tags, _ = c.tags_from_stable(fetch, overrides={'vernemq': 'prod'})
        assert tags['x86']['vernemq'] == 'prod'
        assert 'vernemq' not in asked

    def test_an_unanswerable_component_is_reported_not_guessed(self):
        """vernemq is absent from the endpoint entirely. Without an override it
        must be named, never defaulted."""
        tags, unresolved = c.tags_from_stable(
            lambda comp, arch: None if comp == 'vernemq' else '1.0')
        # x86 only: vernemq is not foundational on arm, so it is never asked for.
        assert unresolved == ['vernemq on x86']

    def test_every_other_component_still_resolves(self):
        tags, unresolved = c.tags_from_stable(
            lambda comp, arch: None if comp == 'vernemq' else '1.0')
        assert len(unresolved) == 1
        assert tags['x86']['backend'] == '1.0'

    def test_arches_can_disagree(self):
        tags, _ = c.tags_from_stable(
            lambda comp, arch: '1.97' if arch == 'x86' else '1.93',
            overrides={'vernemq': 'prod'})
        assert tags['x86']['backend'] == '1.97'
        assert tags['arm']['backend'] == '1.93'


class TestCut:

    def _tags(self):
        return {arch: {comp: '1.0' for comp in m.foundational_for_arch(arch)}
                for arch in m.ARCHES}

    def _editor(self, template):
        return ('security: no\n\nsummary:\nmqtt reconnect fix\n\n'
                'impact:\ncapdev restarts once\n')

    def test_it_produces_a_signable_manifest_and_signature(self, tmp_path):
        signable, signature = c.cut(
            tags=self._tags(), version_text='1.9', work_dir=str(tmp_path),
            key_path='k.pem', resolver=resolver, now=NOW,
            editor=self._editor, confirm=lambda text, rel: True,
            signer=lambda path, key: b'SIG\n',
            existing_tags=[], head=HEAD, reserve=FAKE_RESERVE)
        assert signature == b'SIG\n'
        document = json.loads(signable.decode('utf-8'))
        assert document['release'] == '1.9.1'
        assert not m.notes_shortfall(document)

    def test_it_writes_the_artifacts(self, tmp_path):
        c.cut(tags=self._tags(), version_text='1.9', work_dir=str(tmp_path),
              key_path='k.pem', resolver=resolver, now=NOW,
              editor=self._editor, confirm=lambda t, r: True,
              signer=lambda p, k: b'SIG\n', existing_tags=[], head=HEAD, reserve=FAKE_RESERVE)
        for name in ('candidate.json', 'manifest.json', 'manifest.json.sig'):
            assert (tmp_path / name).exists(), name

    def test_the_signed_bytes_are_what_was_written(self, tmp_path):
        signable, _ = c.cut(
            tags=self._tags(), version_text='1.9', work_dir=str(tmp_path),
            key_path='k.pem', resolver=resolver, now=NOW,
            editor=self._editor, confirm=lambda t, r: True,
            signer=lambda p, k: b'SIG\n', existing_tags=[], head=HEAD, reserve=FAKE_RESERVE)
        assert (tmp_path / 'manifest.json').read_bytes() == signable

    def test_the_counter_follows_existing_tags(self, tmp_path):
        signable, _ = c.cut(
            tags=self._tags(), version_text='1.9', work_dir=str(tmp_path),
            key_path='k.pem', resolver=resolver, now=NOW,
            editor=self._editor, confirm=lambda t, r: True,
            signer=lambda p, k: b'SIG\n',
            existing_tags=['release/47'], head=HEAD, reserve=FAKE_RESERVE)
        assert json.loads(signable.decode('utf-8'))['counter'] == 48

    def test_declining_to_sign_stops_it(self, tmp_path):
        with pytest.raises(sign_mod.SignError, match='aborted'):
            c.cut(tags=self._tags(), version_text='1.9', work_dir=str(tmp_path),
                  key_path='k.pem', resolver=resolver, now=NOW,
                  editor=self._editor, confirm=lambda t, r: False,
                  signer=lambda p, k: b'SIG\n', existing_tags=[], head=HEAD, reserve=FAKE_RESERVE)

    def test_empty_notes_stop_it_before_signing(self, tmp_path):
        signed = []
        with pytest.raises(prepare_mod.PrepareError, match='notes are incomplete'):
            c.cut(tags=self._tags(), version_text='1.9', work_dir=str(tmp_path),
                  key_path='k.pem', resolver=resolver, now=NOW,
                  editor=lambda template: template,
                  confirm=lambda t, r: True,
                  signer=lambda p, k: signed.append(1) or b'SIG\n',
                  existing_tags=[], head=HEAD, reserve=FAKE_RESERVE)
        assert not signed

    def test_pinning_an_arch_component_that_does_not_exist_is_refused(self, tmp_path):
        tags = self._tags()
        tags['arm']['visiontools'] = '0.43'
        with pytest.raises(m.ManifestError, match='not built for arm'):
            c.cut(tags=tags, version_text='1.9', work_dir=str(tmp_path),
                  key_path='k.pem', resolver=resolver, now=NOW,
                  editor=self._editor, confirm=lambda t, r: True,
                  signer=lambda p, k: b'SIG\n', existing_tags=[], head=HEAD, reserve=FAKE_RESERVE)


class TestCounterIsReserved:
    """The counter is only trustworthy if a number can never be handed out
    twice. These pin the ordering that guarantees it."""

    def _tags(self):
        return {arch: {comp: '1.0' for comp in m.foundational_for_arch(arch)}
                for arch in m.ARCHES}

    def _editor(self, template):
        return ('security: no\n\nsummary:\nmqtt reconnect fix\n\n'
                'impact:\ncapdev restarts once\n')

    def _cut(self, tmp_path, order, reserve=None, **kwargs):
        def default_reserve(build_no, commit, message=None):
            order.append('reserve')
            return 'refs/tags/release/{}'.format(build_no)

        return c.cut(
            tags=self._tags(), version_text='1.9', work_dir=str(tmp_path),
            key_path='k.pem', resolver=resolver, now=NOW,
            editor=kwargs.pop('editor', self._editor),
            confirm=kwargs.pop('confirm', lambda t, r: True),
            signer=lambda p, k: order.append('sign') or b'SIG\n',
            existing_tags=kwargs.pop('existing_tags', []), head=HEAD,
            reserve=reserve or default_reserve, **kwargs)

    def test_the_number_is_reserved_before_the_signature(self, tmp_path):
        order = []
        self._cut(tmp_path, order)
        assert order == ['reserve', 'sign']

    def test_it_reserves_the_number_it_actually_stamps(self, tmp_path):
        seen = []
        signable, _ = self._cut(
            tmp_path, [],
            reserve=lambda n, commit, message=None: seen.append(n) or 'refs/tags/release/%d' % n,
            existing_tags=['release/47'])
        assert seen == [48]
        assert json.loads(signable.decode('utf-8'))['counter'] == 48

    def test_it_reserves_against_the_commit_the_manifest_pins(self, tmp_path):
        seen = []
        signable, _ = self._cut(
            tmp_path, [],
            reserve=lambda n, commit, message=None: seen.append(commit) or 'refs/tags/release/%d' % n)
        assert seen == [HEAD]
        assert json.loads(signable.decode('utf-8'))['flexrun']['commit'] == HEAD

    def test_a_rejected_reservation_stops_the_cut_unsigned(self, tmp_path):
        """Someone else took the number. Signing anyway would put two different
        releases into the fleet under one counter."""
        order = []

        def taken(build_no, commit, message=None):
            raise build_mod.BuildError('could not reserve release/1')

        with pytest.raises(build_mod.BuildError, match='could not reserve'):
            self._cut(tmp_path, order, reserve=taken)
        assert 'sign' not in order
        assert not (tmp_path / 'manifest.json.sig').exists()

    def test_abandoning_at_the_notes_still_spends_the_number(self, tmp_path):
        """A gap is the acceptable failure. The reservation is already pushed,
        so re-running gets the next number rather than reusing this one."""
        order = []
        with pytest.raises(prepare_mod.PrepareError):
            self._cut(tmp_path, order, editor=lambda template: template)
        assert order == ['reserve']

    def test_declining_to_sign_still_spends_the_number(self, tmp_path):
        order = []
        with pytest.raises(sign_mod.SignError, match='aborted'):
            self._cut(tmp_path, order, confirm=lambda t, r: False)
        assert order == ['reserve']


class TestRemoteTags:

    def _run(self, stdout='', returncode=0, stderr=''):
        def run(argv):
            self.argv = argv
            return type('R', (), {'stdout': stdout, 'returncode': returncode,
                                  'stderr': stderr})()
        return run

    def test_it_reads_release_tags_from_the_remote(self):
        run = self._run(
            'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\trefs/tags/release/1\n'
            'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\trefs/tags/release/12\n')
        assert build_mod.remote_release_tags(run) == ['release/1', 'release/12']

    def test_unrelated_tags_are_ignored(self):
        """v1.9.1 and archive/* already exist in this repo. Letting them into
        the sequence would move the counter by accident."""
        run = self._run(
            'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\trefs/tags/v1.9.1\n'
            'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\trefs/tags/archive/v1.9\n'
            'cccccccccccccccccccccccccccccccccccccccc\trefs/tags/release/3\n')
        assert build_mod.remote_release_tags(run) == ['release/3']

    def test_dereferenced_tag_lines_do_not_double_count(self):
        """Annotated tags list a second ^{} line for the same tag."""
        run = self._run(
            'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\trefs/tags/release/5\n'
            'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\trefs/tags/release/5^{}\n')
        assert build_mod.remote_release_tags(run) == ['release/5']
        assert build_mod.next_build(build_mod.remote_release_tags(run)) == 6

    def test_an_unreachable_remote_is_an_error_not_an_empty_list(self):
        """Returning [] would silently restart the counter at 1 and re-issue
        numbers the fleet has already seen."""
        run = self._run(returncode=128, stderr='could not read from remote')
        with pytest.raises(build_mod.BuildError, match='could not read release tags'):
            build_mod.remote_release_tags(run)

    def _recorder(self, fail_on=None, returncode=1, stderr=''):
        calls = []

        def run(argv):
            calls.append(argv)
            bad = fail_on is not None and fail_on in argv
            return type('R', (), {
                'stdout': '', 'stderr': stderr if bad else '',
                'returncode': returncode if bad else 0})()
        return run, calls

    def test_reserve_creates_an_annotated_tag_then_pushes_it(self):
        run, calls = self._recorder()
        assert build_mod.reserve_build(7, 'c' * 40, run) == 'refs/tags/release/7'
        assert calls[0][:4] == ['git', 'tag', '-a', '-f']
        assert calls[0][-2:] == ['release/7', 'c' * 40]
        assert calls[-1] == ['git', 'push', 'origin', 'refs/tags/release/7']

    def test_the_tag_is_annotated_not_lightweight(self):
        """The whole reservation rests on this. A lightweight tag is just the
        commit sha, so a second cutter on the same HEAD pushes an identical ref
        value, git reports "Everything up-to-date" and exits 0 - claiming
        nothing. An annotated tag is a distinct object, so the second push is
        refused."""
        run, calls = self._recorder()
        build_mod.reserve_build(7, 'c' * 40, run)
        tag_cmd = calls[0]
        assert '-a' in tag_cmd, 'must be annotated or the reservation is a no-op'

    def test_the_message_is_carried_onto_the_tag(self):
        run, calls = self._recorder()
        build_mod.reserve_build(7, 'c' * 40, run, message='release 1.9.7')
        assert 'release 1.9.7' in calls[0]

    def test_a_rejected_push_is_raised_with_what_to_do(self):
        run, _ = self._recorder(fail_on='push',
                                stderr='! [rejected] already exists')
        with pytest.raises(build_mod.BuildError, match='someone else took that number'):
            build_mod.reserve_build(7, 'c' * 40, run)

    def test_a_rejected_push_does_not_leave_the_local_tag_behind(self):
        """A local tag the remote never granted would make the next cut skip a
        number, and worse, look like the number was legitimately taken."""
        run, calls = self._recorder(fail_on='push', stderr='rejected')
        with pytest.raises(build_mod.BuildError):
            build_mod.reserve_build(7, 'c' * 40, run)
        assert ['git', 'tag', '-d', 'release/7'] in calls


class TestPublishBlock:

    class Stream:
        def __init__(self):
            self.text = ''

        def write(self, chunk):
            self.text += chunk

    def test_the_base64_decodes_to_the_manifest(self):
        stream = self.Stream()
        raw = b'{"schema":"x"}\n'
        c.publish_block(raw, b'SIGVALUE\n', 48, stream)
        encoded = stream.text.split("'manifest_b64': '")[1].split("'")[0]
        assert base64.b64decode(encoded) == raw

    def test_it_names_the_counter_and_the_channel_line(self):
        stream = self.Stream()
        c.publish_block(b'{}', b'SIG\n', 48, stream)
        assert '    48: {' in stream.text
        assert "CHANNELS['stable'] = 48" in stream.text

    def test_the_signature_is_stripped_of_its_newline(self):
        stream = self.Stream()
        c.publish_block(b'{}', b'SIG\n', 1, stream)
        assert "'signature':    'SIG'," in stream.text


class TestCli:

    def test_preflight_failure_returns_nonzero_and_does_nothing(self, tmp_path, capsys):
        version = tmp_path / 'VERSION'
        version.write_text('1.9')
        code = c.main(['--from-stable', '--version-file', str(version),
                       '--key', '/nope/cosign.key'])
        assert code == 1
        assert 'nothing was done' in capsys.readouterr().err

    def test_a_missing_version_file_is_reported(self, tmp_path, capsys):
        code = c.main(['--from-stable', '--version-file',
                       str(tmp_path / 'nope')])
        assert code == 1
        assert 'cannot read' in capsys.readouterr().err

    def test_a_component_source_is_required(self):
        with pytest.raises(SystemExit):
            c.main(['--key', 'k'])

    def test_the_two_sources_are_mutually_exclusive(self):
        with pytest.raises(SystemExit):
            c.main(['--from-stable', '--components', 'x.json', '--key', 'k'])


class TestWriteComponents:
    """Seeding the checked-in file. This is what moves the choice of what to
    pin out of a mutable cloud value and into a reviewable commit."""

    def _tags(self):
        return {arch: {comp: '1.0' for comp in m.foundational_for_arch(arch)}
                for arch in m.ARCHES}

    def test_it_writes_valid_json(self, tmp_path):
        path = str(tmp_path / 'components.json')
        c.write_components(path, self._tags(), _Sink())
        assert json.load(open(path))['components']

    def test_it_round_trips_through_the_reader(self, tmp_path):
        from release import build_release as b
        path = str(tmp_path / 'components.json')
        c.write_components(path, self._tags(), _Sink())
        tags, features = b.components_from_file(open(path).read())
        assert m.per_arch_tags(tags) == self._tags()
        assert features == {}

    def test_it_keeps_the_arches_apart(self, tmp_path):
        tags = self._tags()
        tags['x86']['backend'] = '1.97'
        tags['arm']['backend'] = '1.93'
        path = str(tmp_path / 'components.json')
        c.write_components(path, tags, _Sink())
        loaded = json.load(open(path))['components']
        assert loaded['x86']['backend'] == '1.97'
        assert loaded['arm']['backend'] == '1.93'

    def test_it_omits_what_an_arch_does_not_have(self, tmp_path):
        path = str(tmp_path / 'components.json')
        c.write_components(path, self._tags(), _Sink())
        loaded = json.load(open(path))['components']
        assert 'visiontools' not in loaded['arm']
        assert 'vernemq' not in loaded['arm']
        assert 'visiontools' in loaded['x86']

    def test_it_creates_a_missing_directory(self, tmp_path):
        path = str(tmp_path / 'nested' / 'dir' / 'components.json')
        c.write_components(path, self._tags(), _Sink())
        assert os.path.exists(path)

    def test_the_output_is_stable_across_runs(self, tmp_path):
        """A diff of this file should show what changed, not reordering."""
        one, two = str(tmp_path / 'a.json'), str(tmp_path / 'b.json')
        c.write_components(one, self._tags(), _Sink())
        c.write_components(two, self._tags(), _Sink())
        assert open(one).read() == open(two).read()


class TestCompareToStable:
    """Pinning something other than current stable is normal - rolling forward
    or back. It just must never be a surprise."""

    def test_no_differences_when_everything_matches(self):
        tags = {'x86': {'backend': '1.97'}}
        sink = _Sink()
        assert c.compare_to_stable(tags, lambda comp, arch: '1.97', sink) == []
        assert 'matches current stable' in sink.text

    def test_a_difference_is_reported_with_both_versions(self):
        tags = {'x86': {'backend': '1.97'}}
        sink = _Sink()
        diffs = c.compare_to_stable(tags, lambda comp, arch: '1.93', sink)
        assert diffs == [('x86', 'backend', '1.93', '1.97')]
        assert '1.93' in sink.text and '1.97' in sink.text

    def test_it_reports_per_arch(self):
        tags = {'x86': {'backend': '1.97'}, 'arm': {'backend': '1.93'}}
        diffs = c.compare_to_stable(
            tags, lambda comp, arch: '1.97' if arch == 'x86' else '1.90', _Sink())
        assert diffs == [('arm', 'backend', '1.90', '1.93')]

    def test_a_component_the_endpoint_cannot_answer_for_is_not_a_difference(self):
        """vernemq 500s. That is not a divergence, just no opinion."""
        tags = {'x86': {'vernemq': 'dev'}}
        assert c.compare_to_stable(tags, lambda comp, arch: None, _Sink()) == []

    def test_it_does_not_raise_on_a_difference(self):
        """Reporting, not gating - a deliberate rollback must still be cuttable."""
        tags = {'x86': {'backend': '0.9'}}
        c.compare_to_stable(tags, lambda comp, arch: '1.97', _Sink())


class _Sink:
    def __init__(self):
        self.text = ''

    def write(self, chunk):
        self.text += chunk


class TestKmsReachable:
    """Catching an expired token before digest resolution and notes, not after -
    which is where it actually surfaced the first time this ran for real."""

    class Result:
        def __init__(self, returncode=0, stdout='', stderr=''):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def test_an_enabled_key_is_reachable(self):
        ok, detail = c.kms_reachable(
            KMS_REF, run=lambda argv: self.Result(stdout='ENABLED\tEC_SIGN_P256_SHA256\n'))
        assert ok
        assert 'ENABLED' in detail

    def test_it_describes_the_pinned_version(self):
        calls = []

        def run(argv):
            calls.append(argv)
            return self.Result(stdout='ENABLED\n')

        c.kms_reachable(KMS_REF, run=run)
        argv = calls[0]
        assert argv[:5] == ['gcloud', 'kms', 'keys', 'versions', 'describe']
        assert argv[5] == '1'
        assert argv[argv.index('--key') + 1] == 'k'

    def test_expired_credentials_are_named_plainly(self):
        ok, detail = c.kms_reachable(KMS_REF, run=lambda argv: self.Result(
            returncode=1,
            stderr='ERROR: (gcloud.kms...) There was a problem refreshing your '
                   'current auth tokens: Reauthentication failed.'))
        assert not ok
        assert detail == 'gcloud credentials expired'

    def test_a_missing_key_reports_the_error(self):
        ok, detail = c.kms_reachable(KMS_REF, run=lambda argv: self.Result(
            returncode=1, stderr='ERROR: NOT_FOUND: CryptoKeyVersion not found'))
        assert not ok
        assert 'NOT_FOUND' in detail

    def test_a_destroyed_version_is_not_usable(self):
        """A scheduled-for-destruction version still describes, but cannot sign."""
        ok, detail = c.kms_reachable(
            KMS_REF, run=lambda argv: self.Result(stdout='DESTROY_SCHEDULED\n'))
        assert not ok
        assert 'DESTROY_SCHEDULED' in detail

    def test_a_bad_reference_never_calls_gcloud(self):
        calls = []
        ok, _ = c.kms_reachable('gcpkms://projects/p',
                                run=lambda argv: calls.append(argv))
        assert not ok
        assert calls == []

    def test_preflight_surfaces_it_as_a_blocking_check(self):
        chk = named(checks(key_path=KMS_REF,
                           run=git_stub()), 'KMS key reachable')
        assert chk.fatal
