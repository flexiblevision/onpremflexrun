"""The gate that decides whether a release may be applied to a device.

Each check here closes a specific failure the current pipeline is open to, so
the tests are written around those failures rather than around the functions.
"""
import datetime
import hashlib
import pytest

from release import manifest as m
from release import verify as v

NOW = datetime.datetime(2026, 8, 26, 23, 0, 0)
COMMIT = '6647d89c8f9db71c458bc660f78106381b3c7f09'


def fake_resolver(repo, tag):
    return 'sha256:' + hashlib.sha256('{}:{}'.format(repo, tag).encode()).hexdigest()


def make(counter=47, release='1.9.3', now=NOW, valid_days=90):
    tags = {c: release for c in m.FOUNDATIONAL}
    tags['vernemq'] = 'prod'
    return m.build_manifest(release=release, counter=counter, tags=tags,
                            flexrun_commit=COMMIT, resolver=fake_resolver,
                            now=now, valid_days=valid_days)


def raw(**kwargs):
    return m.canonical_bytes(make(**kwargs))


def good_signature(manifest_path, signature_path, public_key_path):
    return None


def bad_signature(manifest_path, signature_path, public_key_path):
    raise v.VerificationError('signature verification failed: bad key')


def call(raw_manifest, arch='x86', high_water=46, now=NOW,
         verifier=good_signature):
    return v.verify(raw_manifest, arch=arch, high_water=high_water,
                    now=now, signature_path='/tmp/sig', public_key_path='/tmp/key',
                    manifest_path='/tmp/manifest.json', verifier=verifier)


def rollback(raw_manifest, arch='x86', known=(46, 47), now=NOW,
             verifier=good_signature):
    return v.verify_rollback(raw_manifest, arch=arch, known_counters=known,
                             now=now, signature_path='/tmp/sig',
                             public_key_path='/tmp/key',
                             manifest_path='/tmp/manifest.json', verifier=verifier)


class TestHappyPath:

    def test_a_valid_newer_signed_release_is_accepted(self):
        parsed = call(raw(counter=47), high_water=46)
        assert parsed['release'] == '1.9.3'
        assert parsed['counter'] == 47

    def test_first_ever_release_is_accepted(self):
        """A device that has never applied a release has counter 0."""
        assert call(raw(counter=1), high_water=0)['counter'] == 1

    def test_both_arches_verify(self):
        for arch in ('x86', 'arm'):
            assert call(raw(), arch=arch)


class TestSignature:

    def test_an_unsigned_manifest_is_refused(self):
        """The whole point: no signature, no apply."""
        with pytest.raises(v.VerificationError, match='no signature'):
            v.verify(raw(), arch='x86', high_water=0, now=NOW)

    def test_a_bad_signature_is_refused(self):
        with pytest.raises(v.VerificationError, match='signature verification failed'):
            call(raw(), verifier=bad_signature)

    def test_signature_is_checked_before_the_contents_are_trusted(self):
        """A manifest that is BOTH badly signed and a rollback must fail on the
        signature - otherwise an attacker learns about our state from which
        error they get back, and we would be reasoning about unverified data."""
        with pytest.raises(v.VerificationError, match='signature verification failed'):
            call(raw(counter=1), high_water=99, verifier=bad_signature)

    def test_missing_key_path_is_refused_rather_than_skipped(self):
        with pytest.raises(v.VerificationError, match='requires manifest_path'):
            v.verify(raw(), arch='x86', high_water=0, now=NOW,
                     signature_path='/tmp/sig', verifier=good_signature)


class TestAntiRollback:
    """A signature stays valid forever, so freshness cannot come from it.
    The comparison is against the HIGH WATER MARK, not what is running - or a
    device that had rolled back could be pushed straight back down."""

    def test_an_older_counter_is_refused(self):
        with pytest.raises(v.VerificationError, match='refusing a rollback'):
            call(raw(counter=40), high_water=46)

    def test_an_equal_counter_is_refused(self):
        """Strictly greater: re-applying the same release is not an upgrade,
        and allowing equality lets a replayed manifest look acceptable."""
        with pytest.raises(v.VerificationError, match='refusing a rollback'):
            call(raw(counter=46), high_water=46)

    def test_the_error_names_both_counters(self):
        with pytest.raises(v.VerificationError) as exc:
            call(raw(counter=40), high_water=46)
        assert '40' in str(exc.value) and '46' in str(exc.value)

    @pytest.mark.parametrize('bad', ['46', None, 1.5, True])
    def test_a_non_integer_installed_counter_is_refused(self, bad):
        """Comparing against a string would make the rollback check silently
        meaningless."""
        with pytest.raises(v.VerificationError, match='high_water must be'):
            call(raw(), high_water=bad)


class TestExpiryIsReportedNotEnforced:
    """Enforcing expiry means a quarter without a release refuses updates
    fleet-wide - an outage you inflict on yourself, and far likelier than the
    freeze attack it defends against. The counter already blocks downgrades."""

    def test_an_expired_manifest_is_still_applied(self):
        expired = raw(now=datetime.datetime(2026, 1, 1), valid_days=30)
        parsed = call(expired, now=NOW)
        assert parsed['release'] == '1.9.3'

    def test_expiry_is_refused_when_enforcement_is_switched_on(self):
        """Available for a customer that requires the hard gate."""
        expired = raw(now=datetime.datetime(2026, 1, 1), valid_days=30)
        with pytest.raises(v.VerificationError, match='expiry enforcement is on'):
            v.verify(expired, arch='x86', high_water=0, now=NOW,
                     signature_path='/tmp/sig', public_key_path='/tmp/key',
                     manifest_path='/tmp/m.json', verifier=good_signature,
                     enforce_expiry=True)

    def test_a_fresh_manifest_passes_with_enforcement_on(self):
        assert v.verify(raw(), arch='x86', high_water=0, now=NOW,
                        signature_path='/tmp/sig', public_key_path='/tmp/key',
                        manifest_path='/tmp/m.json', verifier=good_signature,
                        enforce_expiry=True)

    def test_an_expired_manifest_is_reported_as_expired(self):
        """The freeze has to be findable, since nothing refuses on it."""
        expired = raw(now=datetime.datetime(2026, 1, 1), valid_days=30)
        parsed = call(expired, now=NOW)
        report = v.freshness(parsed, now=NOW)
        assert report['expired'] is True
        assert report['stale'] is True
        assert report['age_days'] > 200

    def test_a_manifest_expiring_tomorrow_is_still_accepted(self):
        nearly = raw(now=NOW - datetime.timedelta(days=89), valid_days=90)
        assert call(nearly, now=NOW)

    def test_internally_inconsistent_dates_are_refused(self):
        built = make()
        built['notAfter'] = '2020-01-01T00:00:00Z'
        built['created'] = '2026-08-26T23:00:00Z'
        with pytest.raises(v.VerificationError):
            call(m.canonical_bytes(built), now=datetime.datetime(2019, 1, 1))

    @pytest.mark.parametrize('bad', ['not-a-date', '2026-08-26', '', None])
    def test_a_malformed_notafter_is_refused(self, bad):
        built = make()
        built['notAfter'] = bad
        with pytest.raises(v.VerificationError, match='ISO-8601'):
            call(m.canonical_bytes(built))


class TestArch:

    def test_a_manifest_without_this_arch_is_refused(self):
        with pytest.raises(v.VerificationError, match='no images for arch'):
            call(raw(), arch='riscv')

    def test_the_error_lists_the_arches_that_are_covered(self):
        with pytest.raises(v.VerificationError) as exc:
            call(raw(), arch='riscv')
        assert 'x86' in str(exc.value) and 'arm' in str(exc.value)


class TestMalformed:

    def test_unparseable_bytes_are_refused_before_anything_else(self):
        with pytest.raises(v.VerificationError, match='malformed manifest'):
            call(b'{ not json')

    def test_a_manifest_missing_images_is_refused(self):
        built = make()
        del built['images']
        with pytest.raises(v.VerificationError, match='malformed manifest'):
            call(m.canonical_bytes(built))

    def test_a_tampered_digest_is_still_structurally_refused(self):
        """Defence in depth: even if a signature check were bypassed, a
        non-digest reference cannot pass validation."""
        built = make()
        built['images']['x86']['backend']['digest'] = 'latest'
        with pytest.raises(v.VerificationError, match='malformed manifest'):
            call(m.canonical_bytes(built))


class TestStaleWarning:

    def test_flags_a_manifest_inside_the_warning_window(self):
        parsed = call(raw(now=NOW - datetime.timedelta(days=80), valid_days=90))
        assert v.is_stale(parsed, now=NOW, warn_days=14) is True

    def test_does_not_flag_a_fresh_manifest(self):
        parsed = call(raw(now=NOW, valid_days=90))
        assert v.is_stale(parsed, now=NOW, warn_days=14) is False


class TestCosignDelegate:
    """verify.py must never implement crypto itself."""

    def test_success_is_silent(self, tmp_path):
        from unittest.mock import patch, MagicMock
        ok = MagicMock(returncode=0, stdout='', stderr='')
        with patch('subprocess.run', return_value=ok) as run:
            assert v.cosign_verify('m.json', 's.sig', 'k.pub') is None
        argv = run.call_args[0][0]
        assert argv[0] == 'cosign' and 'verify-blob' in argv
        assert '--key' in argv and 'k.pub' in argv

    def test_failure_is_raised_with_the_tool_output(self):
        from unittest.mock import patch, MagicMock
        bad = MagicMock(returncode=1, stdout='', stderr='error: invalid signature')
        with patch('subprocess.run', return_value=bad):
            with pytest.raises(v.VerificationError, match='invalid signature'):
                v.cosign_verify('m.json', 's.sig', 'k.pub')


class TestFreshnessReport:
    """What replaces enforcement: a frozen device is found, not broken."""

    def test_reports_age_and_headroom_for_a_fresh_release(self):
        parsed = call(raw(now=NOW, valid_days=90))
        r = v.freshness(parsed, now=NOW)
        assert r['age_days'] == 0
        assert r['days_until_expiry'] == 90
        assert r['expired'] is False
        assert r['stale'] is False

    def test_flags_stale_before_expiry_not_after(self):
        """The warning has to arrive while there is still time to act."""
        parsed = call(raw(now=NOW - datetime.timedelta(days=80), valid_days=90))
        r = v.freshness(parsed, now=NOW, warn_days=14)
        assert r['stale'] is True
        assert r['expired'] is False
        assert r['days_until_expiry'] == 10

    def test_carries_the_release_identity_for_telemetry(self):
        parsed = call(raw(counter=47))
        r = v.freshness(parsed, now=NOW)
        assert r['release'] == '1.9.3'
        assert r['counter'] == 47

    def test_age_grows_with_time(self):
        parsed = call(raw(now=NOW - datetime.timedelta(days=45), valid_days=90))
        assert v.freshness(parsed, now=NOW)['age_days'] == 45


class TestOperatorRollback:
    """A different actor from an attacker: authenticated, deliberate, present.

    The counter requirement is REPLACED, not removed - the target must be a
    release this device has actually run.
    """

    def test_a_previously_run_release_is_accepted(self):
        parsed = rollback(raw(counter=46), known=(46, 47))
        assert parsed['counter'] == 46

    def test_a_release_this_device_never_ran_is_refused(self):
        """Unbounded choice would let an operator walk backwards into a
        release with a known vulnerability."""
        with pytest.raises(v.VerificationError, match='never run on this device'):
            rollback(raw(counter=12), known=(46, 47))

    def test_the_error_lists_what_is_available(self):
        with pytest.raises(v.VerificationError) as exc:
            rollback(raw(counter=12), known=(46, 47))
        assert '46' in str(exc.value) and '47' in str(exc.value)

    def test_a_device_with_no_history_cannot_roll_back(self):
        with pytest.raises(v.VerificationError, match='no recorded releases'):
            rollback(raw(counter=46), known=())

    def test_an_unsigned_rollback_is_still_refused(self):
        """Being deliberate does not exempt it from the signature."""
        with pytest.raises(v.VerificationError, match='no signature'):
            v.verify_rollback(raw(counter=46), arch='x86',
                              known_counters=(46,), now=NOW)

    def test_a_bad_signature_is_still_refused(self):
        with pytest.raises(v.VerificationError, match='signature verification failed'):
            rollback(raw(counter=46), verifier=bad_signature)

    def test_an_arch_mismatch_is_still_refused(self):
        with pytest.raises(v.VerificationError, match='no images for arch'):
            rollback(raw(counter=46), arch='riscv')

    def test_a_malformed_manifest_is_still_refused(self):
        with pytest.raises(v.VerificationError, match='malformed manifest'):
            rollback(b'{ not json')

    @pytest.mark.parametrize('bad', ['x', None, object()])
    def test_non_integer_history_is_refused(self, bad):
        with pytest.raises(v.VerificationError):
            rollback(raw(counter=46), known=(bad,))

    def test_rollback_does_not_go_through_the_automatic_path(self):
        """The whole point: the channel path still refuses the same manifest,
        so a remote actor cannot use this to downgrade a device."""
        old = raw(counter=46)
        assert rollback(old, known=(46, 47))
        with pytest.raises(v.VerificationError, match='refusing a rollback'):
            call(old, high_water=47)


class TestHighWaterSemantics:

    def test_a_device_that_rolled_back_still_refuses_the_release_it_left(self):
        """installed=46 but high_water=47: the channel offering 47 again must
        not be applied automatically."""
        with pytest.raises(v.VerificationError, match='high water mark 47'):
            call(raw(counter=47), high_water=47)

    def test_a_newer_release_is_still_accepted_after_a_rollback(self):
        """Rolling back must not strand the device - 48 > 47 still applies."""
        assert call(raw(counter=48), high_water=47)['counter'] == 48


class TestLocalVerify:
    """The device-side default: real ECDSA, no cosign, no transparency log.

    Real keys on purpose - a stubbed verifier cannot catch a signature-format
    mismatch between the signer and the device, which is the failure that would
    reject every release in the fleet at once.
    """

    @staticmethod
    def _keypair(tmp_path):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        key = ec.generate_private_key(ec.SECP256R1())
        priv = tmp_path / 'key.pem'
        pub = tmp_path / 'pub.pem'
        priv.write_bytes(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))
        pub.write_bytes(key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo))
        return key, str(priv), str(pub)

    @staticmethod
    def _sign(key, payload, path):
        import base64 as b64
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        der = key.sign(payload, ec.ECDSA(hashes.SHA256()))
        with open(path, 'wb') as handle:
            handle.write(b64.b64encode(der) + b'\n')

    def _signed(self, tmp_path, payload=b'{"a":1}\n'):
        key, priv, pub = self._keypair(tmp_path)
        manifest = tmp_path / 'm.json'
        manifest.write_bytes(payload)
        signature = tmp_path / 'm.json.sig'
        self._sign(key, payload, str(signature))
        return str(manifest), str(signature), pub

    def test_a_good_signature_passes(self, tmp_path):
        manifest, signature, pub = self._signed(tmp_path)
        assert v.local_verify(manifest, signature, pub) is None

    def test_a_changed_manifest_is_rejected(self, tmp_path):
        manifest, signature, pub = self._signed(tmp_path)
        with open(manifest, 'wb') as handle:
            handle.write(b'{"a":2}\n')
        with pytest.raises(v.VerificationError, match='not the one that was signed'):
            v.local_verify(manifest, signature, pub)

    def test_one_byte_matters(self, tmp_path):
        """The trailing newline is inside the signed bytes."""
        manifest, signature, pub = self._signed(tmp_path)
        with open(manifest, 'rb') as handle:
            payload = handle.read()
        with open(manifest, 'wb') as handle:
            handle.write(payload.rstrip(b'\n'))
        with pytest.raises(v.VerificationError):
            v.local_verify(manifest, signature, pub)

    def test_a_different_key_is_rejected(self, tmp_path):
        manifest, signature, _ = self._signed(tmp_path)
        other = tmp_path / 'other'
        other.mkdir()
        _, _, other_pub = self._keypair(other)
        with pytest.raises(v.VerificationError):
            v.local_verify(manifest, signature, other_pub)

    def test_a_corrupt_signature_is_reported_not_crashed(self, tmp_path):
        manifest, signature, pub = self._signed(tmp_path)
        with open(signature, 'wb') as handle:
            handle.write(b'not base64 !!!\n')
        with pytest.raises(v.VerificationError):
            v.local_verify(manifest, signature, pub)

    def test_an_empty_signature_is_rejected(self, tmp_path):
        manifest, signature, pub = self._signed(tmp_path)
        with open(signature, 'wb') as handle:
            handle.write(b'')
        with pytest.raises(v.VerificationError):
            v.local_verify(manifest, signature, pub)

    def test_a_missing_file_is_reported(self, tmp_path):
        manifest, signature, pub = self._signed(tmp_path)
        with pytest.raises(v.VerificationError, match='could not read'):
            v.local_verify(manifest, str(tmp_path / 'nope.sig'), pub)

    def test_an_rsa_key_is_refused_rather_than_misused(self, tmp_path):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        manifest, signature, _ = self._signed(tmp_path)
        rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pub = tmp_path / 'rsa.pem'
        pub.write_bytes(rsa_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo))
        with pytest.raises(v.VerificationError, match='EC public key'):
            v.local_verify(manifest, signature, str(pub))

    def test_verify_uses_it_by_default_so_cosign_is_not_needed(self, tmp_path):
        """A real manifest, a real signature, and no verifier= argument. If the
        default were still cosign this would fail: cosign 3 wants a bundle, not
        a bare base64 signature."""
        key, _, pub = self._keypair(tmp_path)
        payload = raw()
        manifest = tmp_path / 'manifest.json'
        manifest.write_bytes(payload)
        signature = tmp_path / 'manifest.json.sig'
        self._sign(key, payload, str(signature))

        parsed = v.verify(payload, arch='x86', high_water=0, now=NOW,
                          manifest_path=str(manifest),
                          signature_path=str(signature),
                          public_key_path=pub)
        assert parsed['counter'] >= 1
