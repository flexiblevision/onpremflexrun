"""Licence checks.

Most of these pin down the three ways validate_account() fails open: a default
of True, a truthy bound method returned instead of a parsed body, and an
exception handler that answers "licensed" when the cloud cannot be reached.
"""
import datetime
import json
import pytest
from unittest.mock import patch, MagicMock

from addons import entitlements


@pytest.fixture(autouse=True)
def cache(tmp_path, monkeypatch):
    path = tmp_path / 'entitlements.json'
    monkeypatch.setattr(entitlements, 'CACHE_PATH', str(path))
    return path


def addon(tier='enterprise', entitlement='demo_service'):
    return {'name': 'demo', 'tier': tier, 'entitlement': entitlement}


def response(status_code=200, body=True):
    res = MagicMock(status_code=status_code)
    res.json.return_value = body
    return res


class TestIncludedAddons:
    @pytest.mark.unit
    def test_an_included_addon_is_never_checked(self):
        with patch('addons.entitlements.requests.post') as post:
            grant = entitlements.check(addon(tier='included', entitlement=None))

        assert grant.allowed is True
        assert grant.reason == entitlements.INCLUDED
        post.assert_not_called()


class TestTokenClaims:
    """auth.requires_auth verifies the token against the JWKS, so a claim on it
    is signed and needs no network to check."""

    @pytest.mark.unit
    def test_a_listed_service_is_granted_without_asking_the_cloud(self):
        claims = {entitlements.ENTITLEMENTS_CLAIM: ['ocr', 'demo_service']}
        with patch('addons.entitlements.requests.post') as post:
            grant = entitlements.check(addon(), claims=claims)

        assert grant.allowed is True
        assert grant.reason == entitlements.CLAIM
        post.assert_not_called()

    @pytest.mark.unit
    def test_a_claim_that_omits_the_service_is_a_denial(self):
        claims = {entitlements.ENTITLEMENTS_CLAIM: ['ocr']}
        grant = entitlements.check(addon(), claims=claims)

        assert grant.allowed is False
        assert grant.reason == entitlements.DENIED_CLAIM

    @pytest.mark.unit
    def test_a_token_with_no_claim_falls_through(self):
        # Until every token carries one, absent must not read as denied or the
        # whole fleet locks itself out.
        with patch('addons.entitlements.requests.post', return_value=response(body=True)):
            grant = entitlements.check(addon(), 'tok', claims={'sub': 'auth0|1'})

        assert grant.reason == entitlements.CONFIRMED

    @pytest.mark.unit
    def test_a_claim_overrides_a_cached_grant(self):
        with patch('addons.entitlements.requests.post', return_value=response(body=True)):
            entitlements.check(addon(), 'tok')

        claims = {entitlements.ENTITLEMENTS_CLAIM: []}
        assert entitlements.check(addon(), 'tok', claims).allowed is False

    @pytest.mark.unit
    @pytest.mark.parametrize('claims', [
        None, {}, {entitlements.ENTITLEMENTS_CLAIM: 42}, 'not a dict'])
    def test_a_malformed_claim_falls_through(self, claims):
        assert entitlements.from_claims('demo_service', claims) is None

    @pytest.mark.unit
    def test_a_single_string_claim_is_accepted(self):
        claims = {entitlements.ENTITLEMENTS_CLAIM: 'demo_service'}
        assert entitlements.from_claims('demo_service', claims).allowed is True


class TestCloudVerdicts:
    @pytest.mark.unit
    def test_a_yes_is_confirmed_and_cached(self, cache):
        with patch('addons.entitlements.requests.post', return_value=response(body=True)):
            grant = entitlements.check(addon(), 'tok')

        assert grant.allowed is True
        assert grant.reason == entitlements.CONFIRMED
        assert json.loads(cache.read_text())['demo_service']['allowed'] is True

    @pytest.mark.unit
    def test_a_no_is_denied(self):
        with patch('addons.entitlements.requests.post', return_value=response(body=False)):
            grant = entitlements.check(addon(), 'tok')

        assert grant.allowed is False
        assert grant.reason == entitlements.DENIED

    @pytest.mark.unit
    @pytest.mark.parametrize('body,expected', [
        ({'valid': True}, True),
        ({'allowed': False}, False),
        ({'authorized': True}, True),
        ({'entitled': False}, False),
    ])
    def test_an_object_body_is_read_by_its_verdict_key(self, body, expected):
        with patch('addons.entitlements.requests.post', return_value=response(body=body)):
            assert entitlements.check(addon(), 'tok').allowed is expected

    @pytest.mark.unit
    def test_the_response_is_parsed_not_merely_truthy(self):
        # validate_account() returned res.json - the bound method, always
        # truthy - so a "no" from the cloud read as a "yes".
        with patch('addons.entitlements.requests.post', return_value=response(body=False)):
            assert entitlements.check(addon(), 'tok').allowed is False

    @pytest.mark.unit
    def test_an_unparseable_body_is_not_a_licence(self):
        res = MagicMock(status_code=200)
        res.json.side_effect = ValueError('not json')
        with patch('addons.entitlements.requests.post', return_value=res):
            grant = entitlements.check(addon(), 'tok')

        assert grant.allowed is False
        assert grant.reason == entitlements.UNREACHABLE


class TestOffline:
    @pytest.mark.unit
    def test_an_unreachable_cloud_is_not_a_licence(self):
        # validate_account() returned True here.
        with patch('addons.entitlements.requests.post', side_effect=OSError('no route')):
            grant = entitlements.check(addon(), 'tok')

        assert grant.allowed is False
        assert grant.reason == entitlements.UNREACHABLE

    @pytest.mark.unit
    def test_a_non_200_is_not_a_verdict(self):
        with patch('addons.entitlements.requests.post', return_value=response(403)):
            assert entitlements.check(addon(), 'tok').allowed is False

    @pytest.mark.unit
    def test_a_recent_grant_survives_the_cloud_going_away(self):
        # An on-prem device drops off the network routinely; a paid feature must
        # not go down with it.
        with patch('addons.entitlements.requests.post', return_value=response(body=True)):
            entitlements.check(addon(), 'tok')

        with patch('addons.entitlements.requests.post', side_effect=OSError('no route')):
            grant = entitlements.check(addon(), 'tok')

        assert grant.allowed is True
        assert grant.reason == entitlements.CACHED

    @pytest.mark.unit
    def test_a_grant_past_its_grace_window_stops_counting(self, cache):
        stale = datetime.datetime.utcnow() - datetime.timedelta(days=1)
        cache.write_text(json.dumps({'demo_service': {
            'allowed': True,
            'checked_at': '2020-01-01T00:00:00Z',
            'expires_at': entitlements._iso(stale)}}))

        with patch('addons.entitlements.requests.post', side_effect=OSError('no route')):
            grant = entitlements.check(addon(), 'tok')

        assert grant.allowed is False
        assert grant.reason == entitlements.EXPIRED

    @pytest.mark.unit
    def test_a_cached_denial_is_not_re_granted_offline(self):
        with patch('addons.entitlements.requests.post', return_value=response(body=False)):
            entitlements.check(addon(), 'tok')

        with patch('addons.entitlements.requests.post', side_effect=OSError('no route')):
            assert entitlements.check(addon(), 'tok').allowed is False

    @pytest.mark.unit
    def test_no_token_and_no_cache_is_not_a_licence(self):
        grant = entitlements.check(addon())
        assert grant.allowed is False
        assert grant.reason == entitlements.NO_TOKEN

    @pytest.mark.unit
    def test_an_unreadable_cache_is_treated_as_empty(self, cache):
        cache.write_text('{not json')
        assert entitlements.load_cache() == {}


class TestEnforcement:
    @pytest.mark.unit
    def test_nothing_blocks_while_enforcement_is_off(self):
        grant = entitlements.Grant(False, entitlements.DENIED)
        assert entitlements.ENFORCED is False
        assert grant.blocking is False

    @pytest.mark.unit
    def test_a_denial_blocks_once_enforcement_is_on(self, monkeypatch):
        monkeypatch.setattr(entitlements, 'ENFORCED', True)
        assert entitlements.Grant(False, entitlements.DENIED).blocking is True
        assert entitlements.Grant(True, entitlements.CONFIRMED).blocking is False

    @pytest.mark.unit
    def test_a_descriptor_with_no_entitlement_is_refused(self):
        # schema.validate() rejects this, so reaching it means a hand-built
        # descriptor. Refuse rather than assume a licence.
        grant = entitlements.check(addon(entitlement=None), 'tok')
        assert grant.allowed is False
