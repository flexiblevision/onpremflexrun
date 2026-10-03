"""Token validation for the privileged endpoints.

requires_auth is the only thing between an unauthenticated caller and
/shutdown, /restart and /upgrade. The negative cases are the point: a decorator
that accepts a token it should reject is indistinguishable from one that works,
right up until it matters.
"""
import json
import pytest
from unittest.mock import patch, MagicMock

from jose import jwt as jose_jwt

import auth


AUDIENCE = 'https://flexiblevision/api'


def _jwks_bytes(kids=('key-1',)):
    return json.dumps({
        'keys': [
            {'kid': k, 'kty': 'RSA', 'use': 'sig', 'n': 'n-value', 'e': 'AQAB'}
            for k in kids
        ]
    }).encode('utf-8')


@pytest.fixture
def jwks():
    """Patch the local jwks fetch that requires_auth performs."""
    with patch('auth.urlopen') as urlopen:
        urlopen.return_value = MagicMock(read=MagicMock(return_value=_jwks_bytes()))
        yield urlopen


@pytest.fixture
def header():
    """Patch jwt.get_unverified_header to name a kid present in the jwks."""
    with patch.object(auth.jwt, 'get_unverified_header',
                      return_value={'kid': 'key-1', 'alg': 'RS256'}) as h:
        yield h


@pytest.fixture
def decode():
    with patch.object(auth.jwt, 'decode') as d:
        d.return_value = {'sub': 'user-1', 'aud': AUDIENCE,
                          'iss': 'https://' + auth.AUTH0_DOMAIN + '/'}
        yield d


@pytest.fixture
def protected(real_requires_auth):
    """A trivial view wrapped in requires_auth, plus a record of its calls.

    conftest replaces auth.requires_auth with a pass-through for the whole
    session so the route tests do not need tokens; this file needs the real
    one, which conftest hands back through the real_requires_auth fixture.
    """
    calls = []

    @real_requires_auth
    def view(*args, **kwargs):
        from flask import g
        calls.append({'args': args, 'kwargs': kwargs,
                      'user': getattr(g, 'current_user', None)})
        return 'ok'

    view.calls = calls
    return view


def _ctx(token='Bearer abc.def.ghi'):
    headers = {'Authorization': token} if token is not None else {}
    return auth.APP.test_request_context('/protected', headers=headers)


class TestGetTokenAuthHeader:
    """Parsing the Authorization header."""

    @pytest.mark.unit
    @pytest.mark.auth
    def test_extracts_bearer_token(self):
        with _ctx('Bearer abc.def.ghi'):
            assert auth.get_token_auth_header() == 'abc.def.ghi'

    @pytest.mark.unit
    @pytest.mark.auth
    def test_scheme_match_is_case_insensitive(self):
        with _ctx('bearer abc.def.ghi'):
            assert auth.get_token_auth_header() == 'abc.def.ghi'

    @pytest.mark.unit
    @pytest.mark.auth
    def test_missing_header_is_rejected(self):
        with _ctx(None):
            with pytest.raises(auth.AuthError) as e:
                auth.get_token_auth_header()
        assert e.value.status_code == 401
        assert e.value.error['code'] == 'authorization_header_missing'

    @pytest.mark.unit
    @pytest.mark.auth
    def test_wrong_scheme_is_rejected(self):
        with _ctx('Basic dXNlcjpwYXNz'):
            with pytest.raises(auth.AuthError) as e:
                auth.get_token_auth_header()
        assert e.value.status_code == 401
        assert e.value.error['code'] == 'invalid_header'

    @pytest.mark.unit
    @pytest.mark.auth
    def test_scheme_without_token_is_rejected(self):
        with _ctx('Bearer'):
            with pytest.raises(auth.AuthError) as e:
                auth.get_token_auth_header()
        assert e.value.error['description'] == 'Token not found'

    @pytest.mark.unit
    @pytest.mark.auth
    def test_extra_parts_are_rejected(self):
        with _ctx('Bearer abc def'):
            with pytest.raises(auth.AuthError) as e:
                auth.get_token_auth_header()
        assert e.value.status_code == 401
        assert 'Bearer token' in e.value.error['description']


class TestAuthErrorHandler:
    """AuthError is surfaced to the caller as JSON with its own status."""

    @pytest.mark.unit
    @pytest.mark.auth
    def test_serialises_error_body_and_status(self):
        err = auth.AuthError({'code': 'token_expired', 'description': 'expired'}, 401)
        with auth.APP.test_request_context('/'):
            response = auth.handle_auth_error(err)

        assert response.status_code == 401
        assert json.loads(response.get_data(as_text=True)) == {
            'code': 'token_expired', 'description': 'expired'}

    @pytest.mark.unit
    @pytest.mark.auth
    def test_carries_error_and_status_code(self):
        err = auth.AuthError({'code': 'x'}, 403)
        assert err.error == {'code': 'x'}
        assert err.status_code == 403


class TestRequiresAuthAcceptance:
    """Tokens that should get through."""

    @pytest.mark.unit
    @pytest.mark.auth
    def test_valid_token_calls_the_view(self, jwks, header, decode, protected):
        with _ctx():
            assert protected() == 'ok'
        assert len(protected.calls) == 1

    @pytest.mark.unit
    @pytest.mark.auth
    def test_payload_is_published_on_g_current_user(self, jwks, header, decode, protected):
        decode.return_value = {'sub': 'device-42', 'aud': AUDIENCE,
                               'iss': 'https://' + auth.AUTH0_DOMAIN + '/'}
        with _ctx():
            protected()
        assert protected.calls[0]['user']['sub'] == 'device-42'

    @pytest.mark.unit
    @pytest.mark.auth
    def test_view_arguments_are_forwarded(self, jwks, header, decode, protected):
        with _ctx():
            protected('a', key='b')
        assert protected.calls[0]['args'] == ('a',)
        assert protected.calls[0]['kwargs'] == {'key': 'b'}

    @pytest.mark.unit
    @pytest.mark.auth
    def test_matching_jwks_key_is_passed_to_decode(self, jwks, header, decode, protected):
        with _ctx():
            protected()

        rsa_key = decode.call_args[0][1]
        assert rsa_key == {'kty': 'RSA', 'kid': 'key-1', 'use': 'sig',
                           'n': 'n-value', 'e': 'AQAB'}

    @pytest.mark.unit
    @pytest.mark.auth
    def test_correct_key_is_selected_from_a_multi_key_jwks(self, jwks, decode, protected):
        jwks.return_value.read.return_value = _jwks_bytes(('other', 'key-1', 'spare'))
        with patch.object(auth.jwt, 'get_unverified_header',
                          return_value={'kid': 'key-1'}):
            with _ctx():
                protected()

        assert decode.call_args[0][1]['kid'] == 'key-1'

    @pytest.mark.unit
    @pytest.mark.auth
    def test_client_id_is_an_accepted_audience(self, jwks, header, decode, protected):
        decode.return_value = {'sub': 'u', 'aud': auth.CLIENT_ID,
                               'iss': 'https://' + auth.AUTH0_DOMAIN + '/'}
        with _ctx():
            assert protected() == 'ok'

    @pytest.mark.unit
    @pytest.mark.auth
    def test_legacy_auth0_issuer_is_accepted(self, jwks, header, decode, protected):
        decode.return_value = {'sub': 'u', 'aud': AUDIENCE,
                               'iss': 'https://flexiblevision.auth0.com/'}
        with _ctx():
            assert protected() == 'ok'

    @pytest.mark.unit
    @pytest.mark.auth
    def test_audience_list_is_accepted_when_one_entry_matches(self, jwks, header, decode, protected):
        decode.return_value = {'sub': 'u', 'aud': ['something-else', AUDIENCE],
                               'iss': 'https://' + auth.AUTH0_DOMAIN + '/'}
        with _ctx():
            assert protected() == 'ok'

    @pytest.mark.unit
    @pytest.mark.auth
    def test_expiry_is_not_enforced(self, jwks, header, decode, protected):
        # Devices run offline for long stretches and cannot refresh, so expiry
        # checking is deliberately disabled. Pinned because turning it on
        # silently would lock out the fleet.
        with _ctx():
            protected()
        assert decode.call_args[1]['options']['verify_exp'] is False


class TestRequiresAuthRejection:
    """Tokens that must not get through."""

    @pytest.mark.unit
    @pytest.mark.auth
    def test_missing_authorization_header_is_rejected(self, jwks, header, decode, protected):
        with _ctx(None):
            with pytest.raises(auth.AuthError) as e:
                protected()
        assert e.value.error['code'] == 'authorization_header_missing'
        assert protected.calls == []

    @pytest.mark.unit
    @pytest.mark.auth
    def test_unknown_kid_is_rejected(self, jwks, decode, protected):
        with patch.object(auth.jwt, 'get_unverified_header',
                          return_value={'kid': 'not-in-jwks'}):
            with _ctx():
                with pytest.raises(auth.AuthError) as e:
                    protected()

        assert e.value.status_code == 401
        assert e.value.error['description'] == 'Unable to find appropriate key'
        assert protected.calls == []

    @pytest.mark.unit
    @pytest.mark.auth
    def test_empty_jwks_is_rejected(self, jwks, header, decode, protected):
        jwks.return_value.read.return_value = json.dumps({'keys': []}).encode()
        with _ctx():
            with pytest.raises(auth.AuthError) as e:
                protected()
        assert e.value.error['description'] == 'Unable to find appropriate key'

    @pytest.mark.unit
    @pytest.mark.auth
    def test_foreign_audience_is_rejected(self, jwks, header, decode, protected):
        decode.return_value = {'sub': 'u', 'aud': 'https://someone-elses/api',
                               'iss': 'https://' + auth.AUTH0_DOMAIN + '/'}
        with _ctx():
            with pytest.raises(auth.AuthError) as e:
                protected()

        assert e.value.status_code == 401
        assert e.value.error['code'] == 'invalid_audience'
        assert protected.calls == []

    @pytest.mark.unit
    @pytest.mark.auth
    def test_missing_audience_claim_is_rejected(self, jwks, header, decode, protected):
        decode.return_value = {'sub': 'u', 'iss': 'https://' + auth.AUTH0_DOMAIN + '/'}
        with _ctx():
            with pytest.raises(auth.AuthError) as e:
                protected()
        assert e.value.error['code'] == 'invalid_audience'

    @pytest.mark.unit
    @pytest.mark.auth
    def test_foreign_issuer_is_rejected(self, jwks, header, decode, protected):
        decode.return_value = {'sub': 'u', 'aud': AUDIENCE,
                               'iss': 'https://evil.example.com/'}
        with _ctx():
            with pytest.raises(auth.AuthError) as e:
                protected()

        assert e.value.status_code == 401
        assert e.value.error['code'] == 'invalid_issuer'
        assert protected.calls == []

    @pytest.mark.unit
    @pytest.mark.auth
    def test_expired_signature_is_reported_as_expired(self, jwks, header, decode, protected):
        decode.side_effect = jose_jwt.ExpiredSignatureError('expired')
        with _ctx():
            with pytest.raises(auth.AuthError) as e:
                protected()

        assert e.value.error['code'] == 'token_expired'
        assert e.value.status_code == 401

    @pytest.mark.unit
    @pytest.mark.auth
    def test_undecodable_token_is_reported_as_invalid_header(self, jwks, header, decode, protected):
        decode.side_effect = ValueError('not a jwt')
        with _ctx():
            with pytest.raises(auth.AuthError) as e:
                protected()

        assert e.value.error['code'] == 'invalid_header'
        assert 'Unable to parse' in e.value.error['description']

    @pytest.mark.unit
    @pytest.mark.auth
    def test_audience_rejection_is_not_masked_by_the_generic_handler(self, jwks, header, decode, protected):
        # The bare `except Exception` sits after `except AuthError: raise`. If
        # that ordering is ever lost, a rejected audience would be reported as
        # a parse failure instead - same status, wrong reason.
        decode.return_value = {'sub': 'u', 'aud': 'nope', 'iss': 'https://x/'}
        with _ctx():
            with pytest.raises(auth.AuthError) as e:
                protected()
        assert e.value.error['code'] == 'invalid_audience'

    @pytest.mark.unit
    @pytest.mark.auth
    def test_unreachable_jwks_endpoint_propagates(self, header, decode, protected):
        # Not wrapped in AuthError: a local jwks fetch failure is a server
        # fault, not a bad token, and surfaces as a 500 rather than a 401.
        with patch('auth.urlopen', side_effect=IOError('connection refused')):
            with _ctx():
                with pytest.raises(IOError):
                    protected()
        assert protected.calls == []


class TestRequiresAuthLocalEnvironment:
    """ENVIRON == 'local' swaps Auth0 verification for the shared secret."""

    @pytest.mark.unit
    @pytest.mark.auth
    def test_local_env_decodes_with_the_shared_secret(self, jwks, header, protected):
        with patch.object(auth, 'ENVIRON', 'local'), \
             patch.object(auth.jwt, 'decode', return_value={'sub': 'local-user'}) as decode:
            with _ctx():
                assert protected() == 'ok'

        import settings
        assert decode.call_args[0][1] == [settings.config['jwt_secret_key']]
        assert decode.call_args[1]['audience'] == AUDIENCE
        assert decode.call_args[1]['algorithms'] == auth.ALGORITHMS

    @pytest.mark.unit
    @pytest.mark.auth
    def test_local_env_skips_jwks_key_matching(self, jwks, protected):
        # No kid needs to resolve against the jwks in local mode.
        with patch.object(auth, 'ENVIRON', 'local'), \
             patch.object(auth.jwt, 'get_unverified_header', return_value={'kid': 'unknown'}), \
             patch.object(auth.jwt, 'decode', return_value={'sub': 'u'}):
            with _ctx():
                assert protected() == 'ok'

    @pytest.mark.unit
    @pytest.mark.auth
    def test_local_env_still_rejects_a_bad_signature(self, jwks, header, protected):
        with patch.object(auth, 'ENVIRON', 'local'), \
             patch.object(auth.jwt, 'decode', side_effect=ValueError('bad signature')):
            with _ctx():
                with pytest.raises(auth.AuthError) as e:
                    protected()
        assert e.value.error['code'] == 'invalid_header'


class TestAuthConfiguration:
    """Module constants come from settings with documented fallbacks."""

    @pytest.mark.unit
    @pytest.mark.auth
    def test_audience_is_the_flexiblevision_api(self):
        assert auth.AUDIENCE == 'https://flexiblevision/api'

    @pytest.mark.unit
    @pytest.mark.auth
    def test_algorithms_is_a_list(self):
        # jose requires a sequence here; a bare string would silently match
        # single characters.
        assert isinstance(auth.ALGORITHMS, list)
        assert auth.ALGORITHMS == ['RS256']

    @pytest.mark.unit
    @pytest.mark.auth
    def test_decorator_preserves_the_wrapped_function_identity(self, real_requires_auth):
        @real_requires_auth
        def some_view():
            """docstring"""

        assert some_view.__name__ == 'some_view'
        assert some_view.__doc__ == 'docstring'
