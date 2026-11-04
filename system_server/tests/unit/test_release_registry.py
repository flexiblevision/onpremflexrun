"""Digest resolution against Docker Hub.

No network: the HTTP layer is stubbed. What is worth testing is that the
authoritative header is used, that a missing image is a hard failure rather
than a silent gap in a release, and that credentials come from the environment.
"""
import pytest
from unittest.mock import MagicMock

from release import registry as r


def response(status=200, headers=None, json_body=None):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers or {}
    resp.json.return_value = json_body or {}
    return resp


def session(token_resp=None, manifest_resp=None):
    s = MagicMock()
    s.get.return_value = token_resp or response(200, json_body={'token': 't0k'})
    s.head.return_value = manifest_resp or response(
        200, headers={'Docker-Content-Digest': 'sha256:' + 'a' * 64})
    return s


DIGEST = 'sha256:' + 'a' * 64


class TestResolution:

    def test_uses_the_registry_content_digest_header(self):
        """The header is what a client would pull by; a locally computed hash
        would be a different number and would pin nothing."""
        s = session()
        resolver = r.DockerHubResolver(username='u', password='p', session=s)
        assert resolver('fvonprem/x86-backend', '1.9.3') == DIGEST

    def test_requests_the_manifest_by_tag(self):
        s = session()
        r.DockerHubResolver(username='u', password='p', session=s)(
            'fvonprem/arm-vision', '1.9.2')
        url = s.head.call_args[0][0]
        assert url.endswith('/v2/fvonprem/arm-vision/manifests/1.9.2')

    def test_accepts_manifest_lists_and_oci(self):
        """Without these the registry may convert to v1, whose digest differs."""
        s = session()
        r.DockerHubResolver(username='u', password='p', session=s)(
            'fvonprem/x86-backend', '1.9.3')
        accept = s.head.call_args[1]['headers']['Accept']
        assert 'manifest.list.v2+json' in accept
        assert 'oci.image.index.v1+json' in accept

    def test_sends_the_bearer_token(self):
        s = session()
        r.DockerHubResolver(username='u', password='p', session=s)(
            'fvonprem/x86-backend', '1.9.3')
        assert s.head.call_args[1]['headers']['Authorization'] == 'Bearer t0k'

    def test_token_is_scoped_to_the_repository(self):
        s = session()
        r.DockerHubResolver(username='u', password='p', session=s)(
            'fvonprem/x86-backend', '1.9.3')
        params = s.get.call_args[1]['params']
        assert params['scope'] == 'repository:fvonprem/x86-backend:pull'

    def test_token_is_cached_per_repository(self):
        s = session()
        resolver = r.DockerHubResolver(username='u', password='p', session=s)
        resolver('fvonprem/x86-backend', '1.9.3')
        resolver('fvonprem/x86-backend', '1.9.4')
        assert s.get.call_count == 1, 'token should be fetched once per repository'

    def test_a_different_repository_gets_its_own_token(self):
        s = session()
        resolver = r.DockerHubResolver(username='u', password='p', session=s)
        resolver('fvonprem/x86-backend', '1.9.3')
        resolver('fvonprem/arm-backend', '1.9.3')
        assert s.get.call_count == 2

    def test_strips_whitespace_from_the_digest(self):
        s = session(manifest_resp=response(
            200, headers={'Docker-Content-Digest': ' ' + DIGEST + '\n'}))
        resolver = r.DockerHubResolver(username='u', password='p', session=s)
        assert resolver('fvonprem/x86-backend', '1.9.3') == DIGEST


class TestFailures:

    def test_a_missing_image_is_a_hard_failure(self):
        """A release must not be able to pin an image that was never pushed."""
        s = session(manifest_resp=response(404))
        resolver = r.DockerHubResolver(username='u', password='p', session=s)
        with pytest.raises(r.RegistryError, match='does not exist in the registry'):
            resolver('fvonprem/x86-backend', '9.9.9')

    def test_auth_failure_says_how_to_fix_it(self):
        s = session(token_resp=response(401))
        resolver = r.DockerHubResolver(username=None, password=None, session=s)
        with pytest.raises(r.RegistryError, match='DOCKERHUB_USERNAME'):
            resolver('fvonprem/x86-backend', '1.9.3')

    def test_a_missing_digest_header_is_refused(self):
        """Better to fail than to emit a manifest with a hole in it."""
        s = session(manifest_resp=response(200, headers={}))
        resolver = r.DockerHubResolver(username='u', password='p', session=s)
        with pytest.raises(r.RegistryError, match='Docker-Content-Digest'):
            resolver('fvonprem/x86-backend', '1.9.3')

    def test_an_unexpected_status_is_refused(self):
        s = session(manifest_resp=response(500))
        resolver = r.DockerHubResolver(username='u', password='p', session=s)
        with pytest.raises(r.RegistryError, match='HTTP 500'):
            resolver('fvonprem/x86-backend', '1.9.3')

    def test_a_token_response_with_no_token_is_refused(self):
        s = session(token_resp=response(200, json_body={}))
        resolver = r.DockerHubResolver(username='u', password='p', session=s)
        with pytest.raises(r.RegistryError, match='no token'):
            resolver('fvonprem/x86-backend', '1.9.3')


class TestCredentials:

    def test_read_from_the_environment(self, monkeypatch):
        monkeypatch.setenv('DOCKERHUB_USERNAME', 'envuser')
        monkeypatch.setenv('DOCKERHUB_TOKEN', 'envtoken')
        resolver = r.DockerHubResolver(session=session())
        assert resolver.username == 'envuser'
        assert resolver.password == 'envtoken'

    def test_anonymous_when_unset(self, monkeypatch):
        """Public repositories still resolve; private ones fail loudly."""
        monkeypatch.delenv('DOCKERHUB_USERNAME', raising=False)
        monkeypatch.delenv('DOCKERHUB_TOKEN', raising=False)
        s = session()
        r.DockerHubResolver(session=s)('library/alpine', '3.19')
        assert s.get.call_args[1]['auth'] is None

    def test_credentials_are_sent_as_basic_auth_to_the_token_endpoint_only(self):
        s = session()
        r.DockerHubResolver(username='u', password='p', session=s)(
            'fvonprem/x86-backend', '1.9.3')
        assert s.get.call_args[1]['auth'] == ('u', 'p')
        # The manifest request must carry the bearer token, not the password.
        assert 'auth' not in s.head.call_args[1]


class TestStaticResolver:

    def test_returns_recorded_digests(self):
        resolver = r.StaticResolver({'fvonprem/x86-backend:1.9.3': DIGEST})
        assert resolver('fvonprem/x86-backend', '1.9.3') == DIGEST

    def test_records_what_was_asked_for(self):
        resolver = r.StaticResolver({'a:b': DIGEST})
        try:
            resolver('missing', 'tag')
        except r.RegistryError:
            pass
        assert resolver.calls == [('missing', 'tag')]

    def test_unknown_reference_raises(self):
        resolver = r.StaticResolver({})
        with pytest.raises(r.RegistryError, match='no digest recorded'):
            resolver('fvonprem/x86-backend', '1.9.3')
