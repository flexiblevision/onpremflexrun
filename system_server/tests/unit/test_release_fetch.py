"""Fetching a manifest from the cloud function.

The transport is untrusted, so these are not about trusting the response - they
are about failing in a way an operator can act on, and about never handing
verify.py something it will misread.
"""
import base64
import json

import pytest

from release import fetch as f


class Response:
    def __init__(self, status=200, body=None, text=''):
        self.status_code = status
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError('no json')
        return self._body


class Session:
    def __init__(self, response=None, boom=None):
        self.response = response
        self.boom = boom
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({'url': url, 'json': json})
        if self.boom:
            raise self.boom
        return self.response


def envelope(counter=1, arch='x86', raw=None, signature='SIG=='):
    raw = raw if raw is not None else json.dumps(
        {'counter': counter, 'release': '1.9.%d' % counter}).encode()
    return {'schema': 'flexrun.envelope/v1', 'arch': arch, 'channel': 'stable',
            'counter': counter,
            'manifest_b64': base64.b64encode(raw).decode(),
            'signature': signature}


class TestRequestShape:

    def test_it_asks_for_the_channel_by_default(self):
        s = Session(Response(200, envelope()))
        f.fetch_release('x86', session=s)
        assert s.calls[0]['json'] == {'arch': 'x86', 'channel': 'stable'}

    def test_a_counter_replaces_the_channel(self):
        """Asking for a specific release is how a device recovers one it has
        run before - a channel cannot express that."""
        s = Session(Response(200, envelope()))
        f.fetch_release('x86', counter=7, session=s)
        assert s.calls[0]['json'] == {'arch': 'x86', 'counter': 7}

    def test_it_goes_through_the_proxy(self):
        """Never the *.run.app address: functions-proxy is the single IP
        customer firewalls allow."""
        s = Session(Response(200, envelope()))
        f.fetch_release('x86', session=s)
        assert s.calls[0]['url'].startswith(
            'https://functions-proxy.flexiblevision.com/')
        assert s.calls[0]['url'].endswith('/release_manifest')

    def test_arch_is_required(self):
        with pytest.raises(f.FetchError, match='arch is required'):
            f.fetch_release('', session=Session(Response(200, envelope())))


class TestDecoding:

    def test_it_returns_the_decoded_bytes_and_signature(self):
        raw = b'{"counter": 3}'
        s = Session(Response(200, envelope(raw=raw, signature=' SIG== ')))
        got, sig, env = f.fetch_release('x86', session=s)
        assert got == raw
        assert sig == 'SIG=='          # stripped: a stray newline breaks verify
        assert env['counter'] == 1

    def test_bad_base64_is_named(self):
        bad = dict(envelope(), manifest_b64='not base64!!')
        with pytest.raises(f.FetchError, match='not valid base64'):
            f.fetch_release('x86', session=Session(Response(200, bad)))

    def test_an_empty_manifest_is_refused(self):
        """b64 of empty is the empty string, so the presence check catches it
        before decoding. Either way it must not reach verify()."""
        empty = dict(envelope(), manifest_b64=base64.b64encode(b'').decode())
        with pytest.raises(f.FetchError, match='manifest_b64'):
            f.fetch_release('x86', session=Session(Response(200, empty)))

    def test_base64_of_whitespace_is_refused(self):
        blank = dict(envelope(), manifest_b64=base64.b64encode(b'   ').decode())
        raw, _, _ = f.fetch_release('x86', session=Session(Response(200, blank)))
        assert raw.strip() == b''      # verify() rejects it as unparsable JSON

    @pytest.mark.parametrize('missing', ['manifest_b64', 'signature'])
    def test_a_half_envelope_is_refused(self, missing):
        body = dict(envelope())
        body[missing] = ''
        with pytest.raises(f.FetchError, match='no ' + missing):
            f.fetch_release('x86', session=Session(Response(200, body)))


class TestWrongArch:

    def test_being_served_another_arch_is_refused(self):
        """verify() catches this against the signed bytes, but failing here
        names the endpoint instead of surfacing as a signature mismatch."""
        s = Session(Response(200, envelope(arch='arm')))
        with pytest.raises(f.FetchError, match='asked for x86 and was served arm'):
            f.fetch_release('x86', session=s)


class TestFailures:

    def test_an_unreachable_endpoint_says_so(self):
        s = Session(boom=OSError('name resolution failed'))
        with pytest.raises(f.FetchError, match='could not reach'):
            f.fetch_release('x86', session=s)

    def test_404_carries_the_reason(self):
        """Nothing promoted is the normal state of a channel, not a fault."""
        body = {'error': "no release promoted to 'stable' for x86"}
        with pytest.raises(f.FetchError, match='no release promoted'):
            f.fetch_release('x86', session=Session(Response(404, body)))

    def test_a_500_names_the_status(self):
        with pytest.raises(f.FetchError, match='HTTP 500'):
            f.fetch_release('x86', session=Session(Response(500, {})))

    def test_non_json_is_refused(self):
        with pytest.raises(f.FetchError, match='did not return JSON'):
            f.fetch_release('x86', session=Session(Response(200, None)))

    def test_a_json_array_is_refused(self):
        with pytest.raises(f.FetchError, match='expected an object'):
            f.fetch_release('x86', session=Session(Response(200, [1, 2])))


class TestAvailable:
    """Feeds the settings screen, so it must never raise: a device on a factory
    network is offline more often than not, and that is a state to show."""

    def test_offline_is_a_state_not_an_exception(self):
        s = Session(boom=OSError('offline'))
        got = f.available('x86', high_water=3, session=s)
        assert got['reachable'] is False
        assert 'could not reach' in got['detail']

    def test_a_newer_counter_is_flagged(self):
        s = Session(Response(200, envelope(counter=9)))
        got = f.available('x86', high_water=3, session=s)
        assert got['reachable'] and got['counter'] == 9
        assert got['newer_than_installed'] is True

    def test_the_same_counter_is_not_an_update(self):
        s = Session(Response(200, envelope(counter=3)))
        assert f.available('x86', high_water=3, session=s)['newer_than_installed'] is False

    def test_an_older_counter_is_not_an_update(self):
        """Anti-rollback in the shop window: an older release on the channel
        must not be offered as an upgrade."""
        s = Session(Response(200, envelope(counter=1)))
        assert f.available('x86', high_water=5, session=s)['newer_than_installed'] is False

    def test_nothing_published_is_reported_not_raised(self):
        s = Session(Response(404, {'error': 'no release promoted'}))
        assert f.available('x86', high_water=0, session=s)['reachable'] is False

    def test_an_unparsable_manifest_does_not_crash_the_screen(self):
        s = Session(Response(200, envelope(raw=b'not json')))
        got = f.available('x86', high_water=0, session=s)
        assert got['reachable'] is True
        assert 'unparsable' in got['detail']
