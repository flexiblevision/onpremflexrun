"""The device trust store.

Real keys throughout. The failure this guards against is a device left trusting
nothing, which cannot be fixed remotely by definition - so the tests that matter
most are the refusals.
"""
import os
import pytest

from release import trust as t
from release import verify as v


def keypair(tmp_path, name='k'):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo)
    return key, pem


def store(tmp_path, count=1):
    """A trust directory holding `count` real keys."""
    directory = tmp_path / 'keys'
    directory.mkdir()
    made = []
    for index in range(count):
        _, pem = keypair(tmp_path)
        name = 'release-{}.pem'.format(index)
        t.provision(str(directory), name, pem)
        made.append((name, pem, t.fingerprint(pem)))
    return str(directory), made


class TestFingerprint:

    def test_it_is_stable_for_the_same_key(self, tmp_path):
        _, pem = keypair(tmp_path)
        assert t.fingerprint(pem) == t.fingerprint(pem)

    def test_it_ignores_a_trailing_newline(self, tmp_path):
        """Taken over the DER, so re-wrapping the PEM does not change the id."""
        _, pem = keypair(tmp_path)
        assert t.fingerprint(pem) == t.fingerprint(pem.rstrip(b'\n'))

    def test_different_keys_differ(self, tmp_path):
        _, one = keypair(tmp_path)
        _, two = keypair(tmp_path)
        assert t.fingerprint(one) != t.fingerprint(two)

    def test_it_is_short_enough_to_read_out(self, tmp_path):
        _, pem = keypair(tmp_path)
        assert len(t.fingerprint(pem)) == 16

    @pytest.mark.parametrize('bad', [b'', b'not a key', b'-----BEGIN PUBLIC KEY-----\nx\n'])
    def test_junk_is_refused(self, bad):
        with pytest.raises(t.TrustError, match='not a PEM public key'):
            t.fingerprint(bad)


class TestProvision:

    def test_it_creates_the_directory(self, tmp_path):
        _, pem = keypair(tmp_path)
        directory = str(tmp_path / 'etc' / 'flexrun' / 'keys')
        t.provision(directory, 'release.pem', pem)
        assert os.path.isdir(directory)

    def test_the_key_is_then_trusted(self, tmp_path):
        directory, made = store(tmp_path)
        assert made[0][2] in t.state(directory)

    def test_re_provisioning_the_same_key_is_a_no_op(self, tmp_path):
        directory, made = store(tmp_path)
        name, pem, _ = made[0]
        t.provision(directory, name, pem)
        assert len(t.state(directory)) == 1

    def test_a_different_key_under_the_same_name_is_refused(self, tmp_path):
        """A silent trust-anchor swap is exactly what must not be possible."""
        directory, made = store(tmp_path)
        _, other = keypair(tmp_path)
        with pytest.raises(t.TrustError, match='already holds a different key'):
            t.provision(directory, made[0][0], other)

    def test_a_name_that_would_not_be_loaded_is_refused(self, tmp_path):
        _, pem = keypair(tmp_path)
        with pytest.raises(t.TrustError, match='must end in'):
            t.provision(str(tmp_path / 'keys'), 'release.txt', pem)

    def test_junk_is_never_written(self, tmp_path):
        directory = str(tmp_path / 'keys')
        with pytest.raises(t.TrustError):
            t.provision(directory, 'bad.pem', b'not a key')
        assert t.state(directory) == {}


class TestState:

    def test_an_absent_directory_is_empty_not_an_error(self, tmp_path):
        assert t.state(str(tmp_path / 'nothing')) == {}

    def test_it_reports_every_key(self, tmp_path):
        directory, made = store(tmp_path, count=3)
        assert set(t.state(directory)) == {fp for _, _, fp in made}

    def test_a_stray_file_is_ignored(self, tmp_path):
        directory, _ = store(tmp_path)
        (tmp_path / 'keys' / 'README.txt').write_text('notes')
        assert len(t.state(directory)) == 1

    def test_a_corrupt_key_file_does_not_hide_the_good_ones(self, tmp_path):
        """One unreadable file must not make a device untrusting."""
        directory, made = store(tmp_path, count=2)
        (tmp_path / 'keys' / 'broken.pem').write_bytes(b'garbage')
        assert len(t.state(directory)) == 2

    def test_summary_is_reportable(self, tmp_path):
        directory, made = store(tmp_path, count=2)
        report = t.summary(directory)
        assert report['count'] == 2
        assert sorted(k['fingerprint'] for k in report['keys']) == \
            sorted(fp for _, _, fp in made)


class TestApplyUpdate:
    """The rotation mechanism."""

    def test_adding_a_key_keeps_the_old_one(self, tmp_path):
        """Step 1 of a rotation: both keys valid, nothing lost."""
        directory, made = store(tmp_path)
        _, new = keypair(tmp_path)
        resulting = t.apply_update(directory, add=[('release-b.pem', new)])
        assert set(resulting) == {made[0][2], t.fingerprint(new)}

    def test_removing_the_old_key_after_the_new_one_lands(self, tmp_path):
        """Step 4: retire A once B is in place."""
        directory, made = store(tmp_path)
        _, new = keypair(tmp_path)
        t.apply_update(directory, add=[('release-b.pem', new)])
        resulting = t.apply_update(directory, remove=[made[0][2]])
        assert set(resulting) == {t.fingerprint(new)}

    def test_emptying_the_store_is_refused(self, tmp_path):
        """The one way this design could strand a device."""
        directory, made = store(tmp_path)
        with pytest.raises(t.TrustError, match='no trusted key'):
            t.apply_update(directory, remove=[made[0][2]])

    def test_removing_every_key_at_once_is_refused(self, tmp_path):
        directory, made = store(tmp_path, count=3)
        with pytest.raises(t.TrustError, match='no trusted key'):
            t.apply_update(directory, remove=[fp for _, _, fp in made])

    def test_a_swap_in_one_call_is_allowed(self, tmp_path):
        """Add and remove together is fine as long as something remains."""
        directory, made = store(tmp_path)
        _, new = keypair(tmp_path)
        resulting = t.apply_update(directory,
                                   add=[('release-b.pem', new)],
                                   remove=[made[0][2]])
        assert set(resulting) == {t.fingerprint(new)}

    def test_nothing_is_written_when_the_update_is_refused(self, tmp_path):
        directory, made = store(tmp_path)
        before = t.state(directory)
        with pytest.raises(t.TrustError):
            t.apply_update(directory, remove=[made[0][2]])
        assert t.state(directory) == before

    def test_removing_an_untrusted_key_is_refused(self, tmp_path):
        """Silently ignoring it would report a rotation as done when it is not."""
        directory, _ = store(tmp_path)
        with pytest.raises(t.TrustError, match='does not trust'):
            t.apply_update(directory, remove=['0000000000000000'])

    def test_a_dry_run_changes_nothing(self, tmp_path):
        directory, made = store(tmp_path)
        _, new = keypair(tmp_path)
        preview = t.apply_update(directory, add=[('release-b.pem', new)],
                                 dry_run=True)
        assert t.fingerprint(new) in preview
        assert t.fingerprint(new) not in t.state(directory)

    def test_a_dry_run_still_refuses_an_emptying_update(self, tmp_path):
        directory, made = store(tmp_path)
        with pytest.raises(t.TrustError, match='no trusted key'):
            t.apply_update(directory, remove=[made[0][2]], dry_run=True)

    def test_adding_a_key_that_is_already_trusted_is_idempotent(self, tmp_path):
        directory, made = store(tmp_path)
        name, pem, fp = made[0]
        resulting = t.apply_update(directory, add=[(name, pem)])
        assert set(resulting) == {fp}


class TestVerifyAgainstTheStore:
    """What the device actually does: try every trusted key."""

    def _signed(self, tmp_path, key, payload=b'{"a":1}\n'):
        import base64
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        manifest = tmp_path / 'm.json'
        manifest.write_bytes(payload)
        signature = tmp_path / 'm.json.sig'
        signature.write_bytes(
            base64.b64encode(key.sign(payload, ec.ECDSA(hashes.SHA256()))) + b'\n')
        return str(manifest), str(signature)

    def test_a_release_signed_by_any_trusted_key_is_accepted(self, tmp_path):
        """The property that makes a transition survivable: during rotation,
        releases signed by either key work."""
        directory = tmp_path / 'keys'
        directory.mkdir()
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        keys = []
        for index in range(2):
            key = ec.generate_private_key(ec.SECP256R1())
            pem = key.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo)
            t.provision(str(directory), 'release-{}.pem'.format(index), pem)
            keys.append(key)

        for index, key in enumerate(keys):
            signer_dir = tmp_path / str(index)
            signer_dir.mkdir()
            manifest, signature = self._signed(signer_dir, key)
            used = v.local_verify_any(manifest, signature, str(directory))
            assert used.endswith('.pem')

    def test_an_untrusted_key_is_rejected_and_lists_what_was_tried(self, tmp_path):
        from cryptography.hazmat.primitives.asymmetric import ec
        directory, _ = store(tmp_path, count=2)
        stranger = ec.generate_private_key(ec.SECP256R1())
        manifest, signature = self._signed(tmp_path, stranger)
        with pytest.raises(v.VerificationError, match='did not match any of the 2'):
            v.local_verify_any(manifest, signature, directory)

    def test_an_empty_store_is_a_clear_error(self, tmp_path):
        directory = tmp_path / 'keys'
        directory.mkdir()
        from cryptography.hazmat.primitives.asymmetric import ec
        manifest, signature = self._signed(tmp_path,
                                           ec.generate_private_key(ec.SECP256R1()))
        with pytest.raises(v.VerificationError, match='no release public key'):
            v.local_verify_any(manifest, signature, str(directory))

    def test_a_single_file_still_works(self, tmp_path):
        """Backward compatible: one key path, not a directory."""
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        key = ec.generate_private_key(ec.SECP256R1())
        pub = tmp_path / 'only.pem'
        pub.write_bytes(key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo))
        manifest, signature = self._signed(tmp_path, key)
        assert v.local_verify_any(manifest, signature, str(pub)) == str(pub)

    def test_a_list_of_keys_works(self, tmp_path):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        key = ec.generate_private_key(ec.SECP256R1())
        pub = tmp_path / 'only.pem'
        pub.write_bytes(key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo))
        manifest, signature = self._signed(tmp_path, key)
        assert v.local_verify_any(manifest, signature, [str(pub)]) == str(pub)
