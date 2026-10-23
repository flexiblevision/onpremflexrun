"""Signing. Every refusal must happen before the delegate is called, so the
delegate is a spy and a regression reads as "it signed anyway"."""
import base64
import datetime
import hashlib
import json
import os
import pytest

from release import build_release as b
from release import manifest as m
from release import sign as s

NOW = datetime.datetime(2026, 8, 26, 23, 0, 0)
COMMIT = 'a' * 40


def resolver(repo, tag):
    return 'sha256:' + hashlib.sha256('{}:{}'.format(repo, tag).encode()).hexdigest()


def signable(notes=None, features=None, existing_tags=()):
    """A manifest that passes every gate, as canonical bytes."""
    document, _ = b.build('1 1', list(existing_tags), COMMIT,
                          {c: '1.9.2' for c in m.FOUNDATIONAL},
                          resolver, NOW, features=features)
    filled = m.blank_notes()
    filled.update({'summary': 'mqtt reconnect fix',
                   'impact': 'capdev restarts once'})
    filled.update(notes or {})
    document['notes'] = m.normalise_notes(filled)
    return m.canonical_bytes(document)


class Spy:
    """A signing delegate that records whether it was reached."""

    def __init__(self, signature=b'BASE64SIGNATURE', returncode=0):
        self.calls = []
        self.signature = signature
        self.returncode = returncode

    def __call__(self, manifest_path, key_ref):
        self.calls.append((manifest_path, key_ref))
        if self.returncode != 0:
            raise s.SignError('delegate refused')
        return self.signature

    @property
    def signed(self):
        return bool(self.calls)


def yes(text, release):
    return True


def no(text, release):
    return False


class TestCheckSignable:

    def test_a_prepared_manifest_passes(self):
        parsed = s.check_signable(signable())
        assert parsed['release'] == '1.0'

    def test_accepts_text_as_well_as_bytes(self):
        assert s.check_signable(signable().decode('utf-8'))['counter'] == 1

    def test_non_canonical_bytes_are_refused(self):
        """Re-indented by hand, or written by json.dumps with defaults."""
        document = json.loads(signable().decode('utf-8'))
        pretty = json.dumps(document, indent=2).encode('utf-8')
        with pytest.raises(s.SignError, match='non-canonical'):
            s.check_signable(pretty)

    def test_reordered_keys_are_refused(self):
        """Reversed rather than insertion-ordered, so this keeps testing
        something if build_manifest is rewritten."""
        document = json.loads(signable().decode('utf-8'))
        reversed_keys = {key: document[key] for key in sorted(document, reverse=True)}
        reordered = json.dumps(reversed_keys, sort_keys=False,
                               separators=(',', ':')).encode('utf-8') + b'\n'
        assert reordered != m.canonical_bytes(document), 'test built canonical bytes'
        with pytest.raises(s.SignError, match='non-canonical'):
            s.check_signable(reordered)

    def test_a_trailing_newline_difference_is_refused(self):
        """One trailing newline exactly; stripping it changes what is signed."""
        with pytest.raises(s.SignError, match='non-canonical'):
            s.check_signable(signable().rstrip(b'\n'))

    def test_missing_notes_are_refused(self):
        with pytest.raises(s.SignError, match='incomplete notes'):
            s.check_signable(signable(notes={'summary': '', 'impact': ''}))

    def test_a_missing_summary_alone_is_refused(self):
        with pytest.raises(s.SignError, match='incomplete notes'):
            s.check_signable(signable(notes={'summary': ''}))

    def test_a_hand_edited_manifest_cannot_slip_past_prepare(self):
        """prepare.py is the convenient gate; this one holds regardless."""
        document, _ = b.build('1 1', [], COMMIT,
                              {c: '1.9.2' for c in m.FOUNDATIONAL},
                              resolver, NOW)
        with pytest.raises(s.SignError, match='incomplete notes'):
            s.check_signable(m.canonical_bytes(document))

    def test_a_malformed_manifest_is_refused(self):
        with pytest.raises((s.SignError, m.ManifestError)):
            s.check_signable(b'{"schema": "wrong"}')

    def test_garbage_is_refused(self):
        with pytest.raises((s.SignError, m.ManifestError)):
            s.check_signable(b'not json')


class TestConfirmationText:

    def test_shows_the_release_and_counter(self):
        text = s.confirmation_text(json.loads(signable().decode('utf-8')))
        assert '1.0' in text
        assert 'counter 1' in text

    def test_shows_every_component_digest(self):
        parsed = json.loads(signable().decode('utf-8'))
        text = s.confirmation_text(parsed)
        for name, image in m.components_for(parsed, 'x86').items():
            assert name in text
            assert image['digest'].replace('sha256:', '')[:12] in text

    def test_shows_the_notes_the_operator_will_read(self):
        text = s.confirmation_text(json.loads(signable().decode('utf-8')))
        assert 'mqtt reconnect fix' in text
        assert 'capdev restarts once' in text

    def test_a_security_release_is_shouted(self):
        text = s.confirmation_text(json.loads(
            signable(notes={'security': True}).decode('utf-8')))
        assert 'security  YES' in text

    def test_marks_which_components_changed(self):
        raw = signable(notes={'changed': [{'component': 'backend',
                                           'from': '1.0', 'to': '1.9.2'}]})
        text = s.confirmation_text(json.loads(raw.decode('utf-8')))
        backend_line = [line for line in text.splitlines()
                        if line.strip().startswith('backend')][0]
        assert 'CHANGED' in backend_line

    def test_names_the_images_that_were_never_tested(self):
        """The marker must sit on the vendored lines, not just appear on screen."""
        parsed = json.loads(signable().decode('utf-8'))
        text = s.confirmation_text(parsed)
        lines = {line.split()[0]: line for line in text.splitlines()
                 if line.startswith('  ') and line.split()}

        for name in m.components_for(parsed, 'x86'):
            expected = name in m.NOT_TEST_GATED
            assert ('not test-gated' in lines[name]) is expected, name

    def test_shows_features(self):
        raw = signable(features={'eventor': '0.4.1'})
        text = s.confirmation_text(json.loads(raw.decode('utf-8')))
        assert 'eventor' in text

    def test_shows_the_pinned_flexrun_commit(self):
        text = s.confirmation_text(json.loads(signable().decode('utf-8')))
        assert COMMIT in text


class TestSign:

    def test_a_confirmed_release_is_signed(self):
        spy = Spy()
        signature = s.sign(signable(), 'm.json', 'key.pem',
                           confirm=yes, signer=spy)
        assert signature == b'BASE64SIGNATURE'
        assert spy.calls == [('m.json', 'key.pem')]

    def test_declining_signs_nothing(self):
        spy = Spy()
        with pytest.raises(s.SignError, match='aborted'):
            s.sign(signable(), 'm.json', 'key.pem', confirm=no, signer=spy)
        assert not spy.signed

    def test_incomplete_notes_never_reach_the_key(self):
        spy = Spy()
        with pytest.raises(s.SignError, match='incomplete notes'):
            s.sign(signable(notes={'summary': ''}), 'm.json', 'key.pem',
                   confirm=yes, signer=spy)
        assert not spy.signed

    def test_non_canonical_bytes_never_reach_the_key(self):
        spy = Spy()
        document = json.loads(signable().decode('utf-8'))
        with pytest.raises(s.SignError, match='non-canonical'):
            s.sign(json.dumps(document, indent=2), 'm.json', 'key.pem',
                   confirm=yes, signer=spy)
        assert not spy.signed

    def test_a_malformed_manifest_never_reaches_the_key(self):
        spy = Spy()
        with pytest.raises((s.SignError, m.ManifestError)):
            s.sign(b'not json', 'm.json', 'key.pem', confirm=yes, signer=spy)
        assert not spy.signed

    def test_the_confirmation_is_asked_before_signing(self):
        order = []
        spy = Spy()

        def confirm(text, release):
            order.append('confirm')
            return True

        def signer(path, key):
            order.append('sign')
            return b'sig'

        s.sign(signable(), 'm.json', 'key.pem', confirm=confirm, signer=signer)
        assert order == ['confirm', 'sign']

    def test_the_confirmation_is_given_the_release_version(self):
        seen = {}

        def confirm(text, release):
            seen['release'] = release
            return True

        s.sign(signable(), 'm.json', 'key.pem', confirm=confirm,
               signer=Spy())
        assert seen['release'] == '1.0'

    def test_the_key_reference_is_passed_through_untouched(self):
        """A pkcs11 URI for a hardware token must not be mangled into a path."""
        spy = Spy()
        uri = 'pkcs11:token=flexrun;object=release'
        s.sign(signable(), 'm.json', uri, confirm=yes, signer=spy)
        assert spy.calls[0][1] == uri

    def test_a_delegate_failure_is_raised(self):
        with pytest.raises(s.SignError, match='refused'):
            s.sign(signable(), 'm.json', 'key.pem', confirm=yes,
                   signer=Spy(returncode=1))


class TestCosignDelegate:
    """cosign 3.x only emits a bundle - it dropped --output-signature - so the
    raw signature is read back out of it. cosign is never actually run here."""

    class Result:
        def __init__(self, returncode=0, stderr=b''):
            self.returncode = returncode
            self.stdout = b''
            self.stderr = stderr

    def _runner(self, bundle_body=None, returncode=0, stderr=b''):
        calls = []

        def run(argv):
            calls.append(argv)
            if returncode == 0 and bundle_body is not None:
                path = argv[argv.index('--bundle') + 1]
                with open(path, 'w') as handle:
                    handle.write(bundle_body)
            return self.Result(returncode, stderr)

        run.calls = calls
        return run

    def _bundle(self, signature='MEYCIQDizyBm=='):
        return json.dumps({'mediaType': 'application/vnd.dev.sigstore.bundle.v0.3+json',
                           'messageSignature': {'signature': signature}})

    def test_it_asks_for_the_new_bundle_format(self):
        run = self._runner(self._bundle())
        s.cosign_sign('m.json', 'key.pem', run)
        argv = run.calls[0]
        assert argv[:2] == ['cosign', 'sign-blob']
        assert '--new-bundle-format' in argv
        assert '--bundle' in argv
        assert '-y' in argv
        assert argv[-1] == 'm.json'

    def test_the_key_reference_is_passed_through(self):
        run = self._runner(self._bundle())
        s.cosign_sign('m.json', 'pkcs11:token=flexrun', run)
        assert 'pkcs11:token=flexrun' in run.calls[0]

    def test_the_signature_comes_out_of_the_bundle(self):
        run = self._runner(self._bundle('SIGVALUE=='))
        assert s.cosign_sign('m.json', 'k', run) == b'SIGVALUE==\n'

    def test_a_bundle_without_a_message_signature_is_an_error(self):
        """A keyless bundle has no messageSignature, and silently returning
        nothing would write an empty .sig the whole fleet then rejects."""
        run = self._runner(json.dumps({'mediaType': 'x', 'dsseEnvelope': {}}))
        with pytest.raises(s.SignError, match='messageSignature'):
            s.cosign_sign('m.json', 'k', run)

    def test_an_empty_signature_in_the_bundle_is_an_error(self):
        run = self._runner(self._bundle(''))
        with pytest.raises(s.SignError, match='messageSignature'):
            s.cosign_sign('m.json', 'k', run)

    def test_an_unreadable_bundle_is_an_error(self):
        run = self._runner('not json at all')
        with pytest.raises(s.SignError, match='could not read the cosign bundle'):
            s.cosign_sign('m.json', 'k', run)

    def test_a_nonzero_exit_raises_with_the_reason(self):
        run = self._runner(returncode=1, stderr=b'no such token')
        with pytest.raises(s.SignError, match='no such token'):
            s.cosign_sign('m.json', 'k', run)

    def test_the_bundle_can_be_kept(self):
        run = self._runner(self._bundle())
        s.cosign_sign('m.json', 'k', run, bundle_path='/tmp/keep.bundle')
        assert '/tmp/keep.bundle' in run.calls[0]


class TestOpensslSigner:
    """For an offline key: cosign 3 always contacts Rekor and a timestamp
    authority, which a release cut on an air-gapped machine cannot do."""

    class Result:
        def __init__(self, returncode=0, stdout=b'', stderr=b''):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def test_it_returns_base64_der(self):
        signature = s.openssl_signer(
            'm.json', 'k.pem', lambda argv: self.Result(stdout=b'\x30\x44raw'))
        assert base64.b64decode(signature.strip()) == b'\x30\x44raw'

    def test_it_signs_sha256_over_the_file(self):
        calls = []

        def run(argv):
            calls.append(argv)
            return self.Result(stdout=b'der')

        s.openssl_signer('m.json', 'k.pem', run)
        assert calls[0][:4] == ['openssl', 'dgst', '-sha256', '-sign']
        assert calls[0][-1] == 'm.json'

    def test_a_failure_raises_with_the_reason(self):
        with pytest.raises(s.SignError, match='bad password'):
            s.openssl_signer('m.json', 'k', lambda argv: self.Result(
                returncode=1, stderr=b'bad password'))

    def test_an_empty_signature_is_an_error(self):
        with pytest.raises(s.SignError, match='empty signature'):
            s.openssl_signer('m.json', 'k', lambda argv: self.Result(stdout=b''))


class TestCli:

    def _manifest(self, tmp_path, raw=None):
        path = tmp_path / 'manifest.json'
        path.write_bytes(raw if raw is not None else signable())
        return str(path)

    def test_refuses_to_overwrite_an_existing_signature(self, tmp_path, capsys):
        path = self._manifest(tmp_path)
        (tmp_path / 'manifest.json.sig').write_bytes(b'old\n')
        assert s.main([path, '--key', 'k']) == 1
        assert 'already exists' in capsys.readouterr().err
        assert (tmp_path / 'manifest.json.sig').read_bytes() == b'old\n'

    def test_a_missing_manifest_is_reported(self, tmp_path, capsys):
        assert s.main([str(tmp_path / 'nope.json'), '--key', 'k']) == 1
        assert 'could not read' in capsys.readouterr().err

    def test_incomplete_notes_exit_nonzero(self, tmp_path, capsys):
        path = self._manifest(tmp_path, signable(notes={'summary': ''}))
        assert s.main([path, '--key', 'k']) == 1
        assert 'incomplete notes' in capsys.readouterr().err
        assert not os.path.exists(path + '.sig')

    def test_a_key_is_required(self, tmp_path):
        with pytest.raises(SystemExit):
            s.main([self._manifest(tmp_path)])


KMS_REF = ('gcpkms://projects/flexible-vision-staging/locations/us-central1/'
           'keyRings/flexrun-release/cryptoKeys/release-signing/versions/1')


class TestParseKmsKey:

    def test_it_splits_a_well_formed_reference(self):
        fields = s.parse_kms_key(KMS_REF)
        assert fields['projects'] == 'flexible-vision-staging'
        assert fields['locations'] == 'us-central1'
        assert fields['keyRings'] == 'flexrun-release'
        assert fields['cryptoKeys'] == 'release-signing'
        assert fields['versions'] == '1'

    def test_a_trailing_slash_is_tolerated(self):
        assert s.parse_kms_key(KMS_REF + '/')['versions'] == '1'

    @pytest.mark.parametrize('bad', [
        'gcpkms://projects/p/locations/l/keyRings/r/cryptoKeys/k',
        'gcpkms://projects/p',
        'gcpkms://',
    ])
    def test_a_truncated_reference_is_refused(self, bad):
        """Pinning to a key ring without a version would sign with whatever
        version happened to be primary."""
        with pytest.raises(s.SignError, match='malformed'):
            s.parse_kms_key(bad)

    def test_a_misspelled_segment_names_the_position(self):
        bad = KMS_REF.replace('keyRings', 'keyring')
        with pytest.raises(s.SignError, match='keyRings'):
            s.parse_kms_key(bad)

    def test_an_empty_segment_is_refused(self):
        bad = ('gcpkms://projects/p/locations/l/keyRings//cryptoKeys/k/'
               'versions/1')
        with pytest.raises(s.SignError, match='malformed|empty'):
            s.parse_kms_key(bad)

    @pytest.mark.parametrize('bad', ['/etc/cosign.key', 'pkcs11:token=x', '', None])
    def test_a_non_kms_reference_is_refused(self, bad):
        with pytest.raises(s.SignError, match='not a Cloud KMS'):
            s.parse_kms_key(bad)


class TestKmsSigner:
    """gcloud is never run - the delegate is driven through an injected runner."""

    class Result:
        def __init__(self, returncode=0, stderr=b''):
            self.returncode = returncode
            self.stdout = b''
            self.stderr = stderr

    def _runner(self, der=b'\x30\x44derbytes', returncode=0, stderr=b''):
        calls = []

        def run(argv):
            calls.append(argv)
            if returncode == 0:
                path = argv[argv.index('--signature-file') + 1]
                with open(path, 'wb') as handle:
                    handle.write(der)
            return self.Result(returncode, stderr)

        run.calls = calls
        return run

    def test_it_returns_base64_der(self):
        run = self._runner(der=b'\x30\x44raw')
        signature = s.kms_signer('m.json', KMS_REF, run)
        assert base64.b64decode(signature.strip()) == b'\x30\x44raw'

    def test_it_asks_kms_to_digest_with_sha256(self):
        """local_verify checks ECDSA-SHA256, so a different digest here would
        produce a signature the whole fleet rejects."""
        run = self._runner()
        s.kms_signer('m.json', KMS_REF, run)
        argv = run.calls[0]
        assert argv[:3] == ['gcloud', 'kms', 'asymmetric-sign']
        assert argv[argv.index('--digest-algorithm') + 1] == 'sha256'

    def test_it_pins_the_key_version(self):
        run = self._runner()
        s.kms_signer('m.json', KMS_REF, run)
        argv = run.calls[0]
        assert argv[argv.index('--version') + 1] == '1'
        assert argv[argv.index('--keyring') + 1] == 'flexrun-release'
        assert argv[argv.index('--project') + 1] == 'flexible-vision-staging'

    def test_it_signs_the_manifest_file(self):
        run = self._runner()
        s.kms_signer('/tmp/manifest.json', KMS_REF, run)
        argv = run.calls[0]
        assert argv[argv.index('--input-file') + 1] == '/tmp/manifest.json'

    def test_a_gcloud_failure_raises_with_the_reason(self):
        run = self._runner(returncode=1, stderr=b'PERMISSION_DENIED on key')
        with pytest.raises(s.SignError, match='PERMISSION_DENIED'):
            s.kms_signer('m.json', KMS_REF, run)

    def test_an_empty_signature_is_an_error(self):
        run = self._runner(der=b'')
        with pytest.raises(s.SignError, match='empty signature'):
            s.kms_signer('m.json', KMS_REF, run)

    def test_a_bad_reference_never_calls_gcloud(self):
        run = self._runner()
        with pytest.raises(s.SignError):
            s.kms_signer('m.json', 'gcpkms://projects/p', run)
        assert run.calls == []


class TestKmsPublicKey:

    class Result:
        def __init__(self, returncode=0, stderr=b''):
            self.returncode = returncode
            self.stdout = b''
            self.stderr = stderr

    def test_it_writes_to_the_given_path(self):
        calls = []

        def run(argv):
            calls.append(argv)
            return self.Result()

        assert s.kms_public_key(KMS_REF, '/tmp/pub.pem', run) == '/tmp/pub.pem'
        argv = calls[0]
        assert argv[:5] == ['gcloud', 'kms', 'keys', 'versions', 'get-public-key']
        assert argv[5] == '1'
        assert argv[argv.index('--output-file') + 1] == '/tmp/pub.pem'

    def test_a_failure_is_raised(self):
        with pytest.raises(s.SignError, match='could not fetch'):
            s.kms_public_key(KMS_REF, '/tmp/pub.pem',
                             lambda argv: self.Result(1, b'NOT_FOUND'))


class TestSignerFor:

    def test_a_kms_reference_selects_kms(self):
        assert s.signer_for(KMS_REF) is s.kms_signer

    def test_a_cosign_key_selects_cosign(self, tmp_path):
        key = tmp_path / 'cosign.key'
        key.write_text('-----BEGIN ENCRYPTED SIGSTORE PRIVATE KEY-----\nx\n')
        assert s.signer_for(str(key)) is s.cosign_sign

    def test_an_ec_pem_selects_openssl(self, tmp_path):
        key = tmp_path / 'release.key'
        key.write_text('-----BEGIN PRIVATE KEY-----\nx\n')
        assert s.signer_for(str(key)) is s.openssl_signer

    def test_a_pkcs11_uri_falls_back_to_cosign(self):
        """Not a file, not KMS - cosign is what speaks pkcs11."""
        assert s.signer_for('pkcs11:token=flexrun;object=release') is s.cosign_sign

    def test_sign_uses_the_selected_delegate(self, tmp_path):
        key = tmp_path / 'release.key'
        key.write_text('-----BEGIN PRIVATE KEY-----\nx\n')
        calls = []

        def fake_openssl(manifest_path, key_ref, run=None):
            calls.append(key_ref)
            return b'SIG\n'

        original = s.openssl_signer
        s.openssl_signer = fake_openssl
        try:
            # signer_for resolves at call time, so patching is enough
            assert s.signer_for(str(key)) is fake_openssl
            out = s.sign(signable(), 'm.json', str(key), confirm=yes)
            assert out == b'SIG\n'
            assert calls == [str(key)]
        finally:
            s.openssl_signer = original
