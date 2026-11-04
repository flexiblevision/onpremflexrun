"""Writing the registry push token into GitHub Actions secrets.

The value being handled is a credential that can publish to the fleet's
registry, so these are mostly about the ways it could leak or be stored wrong,
not about the happy path.
"""
import base64
import ctypes
import ctypes.util

import pytest

from release import set_ci_secrets as s


def keypair():
    lib = ctypes.CDLL(ctypes.util.find_library('sodium'))
    lib.sodium_init()
    pk, sk = ctypes.create_string_buffer(32), ctypes.create_string_buffer(32)
    assert lib.crypto_box_keypair(pk, sk) == 0
    return lib, pk, sk


def unseal(lib, pk, sk, ciphertext_b64):
    raw = base64.b64decode(ciphertext_b64)
    out = ctypes.create_string_buffer(len(raw) - 48)
    assert lib.crypto_box_seal_open(out, raw, ctypes.c_ulonglong(len(raw)), pk, sk) == 0
    return out.raw.decode()


class TestSeal:
    """GitHub cannot tell us the ciphertext was malformed - it stores whatever
    it is given, and the failure surfaces much later as a login that fails in
    CI. So the encryption has to be right here."""

    def test_it_round_trips(self):
        lib, pk, sk = keypair()
        secret = 'example-credential-value-0123456789'
        assert unseal(lib, pk, sk, s.seal(base64.b64encode(pk.raw).decode(), secret)) == secret

    def test_the_same_value_seals_differently_each_time(self):
        """Sealed boxes use an ephemeral keypair. Identical ciphertexts would
        mean the token could be recognised by comparison."""
        _, pk, _ = keypair()
        key = base64.b64encode(pk.raw).decode()
        assert s.seal(key, 'same') != s.seal(key, 'same')

    def test_a_wrong_length_key_is_refused(self):
        with pytest.raises(s.SecretError, match='expected 32'):
            s.seal(base64.b64encode(b'short').decode(), 'x')

    def test_unicode_survives(self):
        lib, pk, sk = keypair()
        value = 'tökén-ünicode'
        assert unseal(lib, pk, sk, s.seal(base64.b64encode(pk.raw).decode(), value)) == value


class TestTokenIsNeverOnArgv:
    """argv is world-readable through /proc and ps, and lands in shell history.
    A --token flag would be the obvious convenience and the obvious leak."""

    def test_the_parser_has_no_token_option(self):
        with pytest.raises(SystemExit):
            s.main(['--repos', '--token', 'example-credential-value'])

    def test_username_is_the_only_value_argument(self):
        """Username is not a secret, so it may be passed. Nothing else may."""
        import argparse
        parser = argparse.ArgumentParser()
        # Mirror of main()'s parser: assert by inspection of the real one.
        text = open(s.__file__).read()
        assert "add_argument('--username'" in text
        for leaky in ("add_argument('--token'", "add_argument('--pat'",
                      "add_argument('--password'"):
            assert leaky not in text, 'a secret must not be settable from argv'


class TestReadSecret:

    def test_the_environment_is_preferred_over_prompting(self, monkeypatch):
        monkeypatch.setenv('GH_TOKEN', 'from-env')
        monkeypatch.setattr(s.getpass, 'getpass',
                            lambda p: pytest.fail('should not have prompted'))
        assert s.read_secret('x', ('GH_TOKEN',)) == ('from-env', 'GH_TOKEN')

    def test_it_falls_through_empty_variables(self, monkeypatch):
        monkeypatch.setenv('GH_TOKEN', '')
        monkeypatch.setenv('GITHUB_TOKEN', 'second')
        assert s.read_secret('x', ('GH_TOKEN', 'GITHUB_TOKEN'))[0] == 'second'

    def test_it_prompts_without_echo_when_nothing_is_set(self, monkeypatch):
        monkeypatch.delenv('GH_TOKEN', raising=False)
        monkeypatch.setattr(s.sys.stdin, 'isatty', lambda: True)
        monkeypatch.setattr(s.getpass, 'getpass', lambda prompt: '  typed  ')
        assert s.read_secret('x', ('GH_TOKEN',)) == ('typed', 'prompt')

    def test_surrounding_whitespace_is_stripped(self, monkeypatch):
        """A trailing newline from a paste or a pipe would be sent as part of
        the token and fail authentication in CI with nothing to point at."""
        monkeypatch.setenv('GH_TOKEN', ' tok\n')
        assert s.read_secret('x', ('GH_TOKEN',))[0] == 'tok'


class TestErrorMapping:
    """A raw 'HTTP 403' tells you nothing. Each of these has one likely cause
    and it should be named."""

    class Response:
        def __init__(self, status):
            self.status_code = status
            self.text = ''

    @pytest.mark.parametrize('status,expected', [
        (401, 'invalid or expired'),
        (403, 'needs `repo` scope'),
        (404, 'not found'),
    ])
    def test_it_says_what_to_fix(self, status, expected):
        with pytest.raises(s.SecretError, match=expected):
            s._check(self.Response(status), 'thing')

    @pytest.mark.parametrize('status', [200, 201, 204])
    def test_success_codes_pass(self, status):
        assert s._check(self.Response(status), 'thing') is None


class TestOrgSecretVisibility:

    def test_an_org_secret_is_scoped_to_named_repositories(self):
        """'all' would let every repository in the org read a token that can
        publish to the fleet's registry."""
        sent = {}

        class Session:
            def put(self, url, timeout=None, json=None):
                sent.update(json)
                return TestErrorMapping.Response(204)

        _, pk, _ = keypair()
        s.put_org_secret(Session(), 'DOCKERHUB_TOKEN', 'v', 'kid',
                         base64.b64encode(pk.raw).decode(), [1, 2, 3])
        assert sent['visibility'] == 'selected'
        assert sent['selected_repository_ids'] == [1, 2, 3]

    def test_the_plaintext_is_never_in_the_request(self):
        sent = {}

        class Session:
            def put(self, url, timeout=None, json=None):
                sent.update(json)
                return TestErrorMapping.Response(204)

        _, pk, _ = keypair()
        s.put_org_secret(Session(), 'DOCKERHUB_TOKEN', 'super-secret-value',
                         'kid', base64.b64encode(pk.raw).decode(), [1])
        assert 'super-secret-value' not in str(sent)
        assert sent['encrypted_value']


class TestRepoList:

    def test_an_unknown_repo_stops_before_any_credential_is_read(self, monkeypatch):
        monkeypatch.setattr(s, 'read_secret',
                            lambda *a, **k: pytest.fail('asked for a credential'))
        assert s.main(['--repos', '--only', 'not-a-repo']) == 2

    def test_the_known_set_matches_the_repos_with_image_jobs(self):
        assert set(s.REPOS) == {'visionapi', 'onprembackend', 'onpremfrontend',
                                'predictlite', 'visiontools', 'nodecreator'}
