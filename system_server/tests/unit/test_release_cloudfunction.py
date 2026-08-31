"""The release_manifest cloud function.

The load-bearing test here is the byte-for-byte one. The signature covers the
exact manifest bytes, so if the function ever decodes and re-serialises the
manifest - which is what a framework does by default if you hand it a dict -
every signature fails on every device, and it fails as "signature verification
failed" with nothing pointing at the transport. Base64 exists to make that
impossible, and these tests are what keep it that way.
"""
import base64
import datetime
import hashlib
import json
import os
import sys

import pytest

from release import build_release as b
from release import manifest as m

CF_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', '..', '..',
    'release', 'cloudfunction')

NOW = datetime.datetime(2026, 8, 27, 12, 0, 0)
COMMIT = 'a' * 40


@pytest.fixture
def cf():
    """Import the function the way the runtime does: flat directory on path."""
    path = os.path.abspath(CF_DIR)
    sys.path.insert(0, path)
    for name in ('main', 'releases'):
        sys.modules.pop(name, None)
    try:
        import main
        import releases
        main.releases = releases
        releases.RELEASES = {'x86': {}, 'arm': {}}
        releases.CHANNELS = {'x86': {'stable': None, 'beta': None},
                             'arm': {'stable': None, 'beta': None}}
        yield main
    finally:
        for name in ('main', 'releases'):
            sys.modules.pop(name, None)
        if path in sys.path:
            sys.path.remove(path)


class Request:
    """Stands in for flask.Request - the function only calls get_json.

    arch is defaulted here, not in the endpoint: a device always knows its own
    architecture, and guessing one server-side would hand an arm device x86
    images. The endpoint requiring it is asserted separately.
    """

    def __init__(self, body=None, arch='x86'):
        if isinstance(body, dict) and arch is not None and 'arch' not in body:
            body = dict(body, arch=arch)
        self._body = body

    def get_json(self, silent=False):
        return self._body


def resolver(repo, tag):
    return 'sha256:' + hashlib.sha256('{}:{}'.format(repo, tag).encode()).hexdigest()


def signed_manifest():
    """Canonical bytes of a complete, notes-filled manifest."""
    document, _ = b.build('1.9', [], COMMIT,
                          {c: '1.9.2' for c in m.FOUNDATIONAL},
                          resolver, NOW)
    notes = m.blank_notes()
    notes.update({'summary': 'mqtt reconnect fix',
                  'impact': 'capdev restarts once'})
    document['notes'] = m.normalise_notes(notes)
    return m.canonical_bytes(document)


def publish(cf, counter=48, channel='stable', raw=None, signature='SIGBASE64==',
            arch='x86'):
    raw = raw if raw is not None else signed_manifest()
    cf.releases.RELEASES[arch][counter] = {
        'manifest_b64': base64.b64encode(raw).decode('ascii'),
        'signature': signature,
    }
    if channel:
        cf.releases.CHANNELS[arch][channel] = counter
    return raw


def body_of(response):
    return json.loads(response[0])


def status_of(response):
    return response[1]


def headers_of(response):
    return response[2]


class TestByteFidelity:
    """Why this endpoint carries base64 at all."""

    def test_the_manifest_survives_the_round_trip_exactly(self, cf):
        raw = publish(cf)
        returned = body_of(cf.release_manifest(Request({'channel': 'stable'})))
        assert base64.b64decode(returned['manifest_b64']) == raw

    def test_the_trailing_newline_survives(self, cf):
        """canonical_bytes ends in exactly one newline and the signature covers
        it, so a transport that strips it breaks verification."""
        raw = publish(cf)
        assert raw.endswith(b'\n')
        returned = body_of(cf.release_manifest(Request({'channel': 'stable'})))
        assert base64.b64decode(returned['manifest_b64']).endswith(b'\n')

    def test_the_base64_string_is_returned_character_for_character(self, cf):
        raw = signed_manifest()
        encoded = base64.b64encode(raw).decode('ascii')
        publish(cf, raw=raw)
        returned = body_of(cf.release_manifest(Request({'channel': 'stable'})))
        assert returned['manifest_b64'] == encoded

    def test_the_signature_is_returned_verbatim(self, cf):
        publish(cf, signature='MEUCIQDf3n2K8pXm+/aB==')
        returned = body_of(cf.release_manifest(Request({'channel': 'stable'})))
        assert returned['signature'] == 'MEUCIQDf3n2K8pXm+/aB=='

    def test_a_reserialised_manifest_would_not_match(self, cf):
        """The premise, made explicit: handing the manifest back as a JSON
        object instead of base64 changes the bytes, so this is not a
        theoretical concern."""
        raw = signed_manifest()
        naive = json.dumps(json.loads(raw.decode('utf-8'))).encode('utf-8')
        assert naive != raw

    def test_the_response_is_valid_json(self, cf):
        publish(cf)
        response = cf.release_manifest(Request({'channel': 'stable'}))
        assert isinstance(json.loads(response[0]), dict)


class TestChannelLookup:

    def test_a_promoted_channel_is_served(self, cf):
        publish(cf, counter=48)
        returned = body_of(cf.release_manifest(Request({'channel': 'stable'})))
        assert returned['counter'] == 48
        assert returned['channel'] == 'stable'
        assert returned['schema'] == 'flexrun.release.envelope/v1'

    def test_stable_is_the_default_channel(self, cf):
        publish(cf)
        assert status_of(cf.release_manifest(Request({}))) == 200

    def test_no_body_at_all_is_refused(self, cf):
        """Used to serve stable. It cannot any more: arch is not guessable, and
        defaulting to x86 would hand an arm device images that do not exist for
        it, carrying a counter from a sequence it does not follow."""
        publish(cf)
        response = cf.release_manifest(Request(None, arch=None))
        assert status_of(response) == 400
        assert 'arch' in body_of(response)['error']

    def test_an_unknown_arch_is_refused_and_lists_the_real_ones(self, cf):
        publish(cf)
        response = cf.release_manifest(Request({'arch': 'riscv'}, arch=None))
        assert status_of(response) == 400
        assert body_of(response)['arches'] == ['arm', 'x86']

    def test_promoting_one_arch_leaves_the_other_alone(self, cf):
        """The whole point of per-arch counters: an x86 release must not make an
        arm device think anything happened."""
        publish(cf, counter=48, arch='x86')
        assert status_of(cf.release_manifest(Request({}, arch='x86'))) == 200
        response = cf.release_manifest(Request({}, arch='arm'))
        assert status_of(response) == 404

    def test_the_same_counter_on_two_arches_is_two_releases(self, cf):
        publish(cf, counter=1, arch='x86', signature='X86SIG==')
        publish(cf, counter=1, arch='arm', signature='ARMSIG==')
        x86 = body_of(cf.release_manifest(Request({}, arch='x86')))
        arm = body_of(cf.release_manifest(Request({}, arch='arm')))
        assert x86['counter'] == arm['counter'] == 1
        assert x86['signature'] != arm['signature']
        assert x86['arch'] == 'x86' and arm['arch'] == 'arm'

    def test_an_unpromoted_channel_is_a_404(self, cf):
        publish(cf, channel='stable')
        response = cf.release_manifest(Request({'channel': 'beta'}))
        assert status_of(response) == 404
        assert 'beta' in body_of(response)['error']

    def test_an_unknown_channel_is_a_404_that_lists_the_real_ones(self, cf):
        response = cf.release_manifest(Request({'channel': 'canary'}))
        assert status_of(response) == 404
        assert body_of(response)['channels'] == ['beta', 'stable']

    def test_channels_are_independent(self, cf):
        publish(cf, counter=48, channel='stable')
        publish(cf, counter=49, channel='beta')
        assert body_of(cf.release_manifest(Request({'channel': 'stable'})))['counter'] == 48
        assert body_of(cf.release_manifest(Request({'channel': 'beta'})))['counter'] == 49

    def test_promotion_is_one_integer(self, cf):
        """The whole promote/rollback mechanism."""
        publish(cf, counter=48, channel='stable')
        publish(cf, counter=49, channel=None)
        cf.releases.CHANNELS['x86']['stable'] = 49
        assert body_of(cf.release_manifest(Request({})))['counter'] == 49
        cf.releases.CHANNELS['x86']['stable'] = 48
        assert body_of(cf.release_manifest(Request({})))['counter'] == 48


class TestCounterLookup:
    """Recovery path for a device that lost its local manifest cache."""

    def test_a_published_counter_is_served_without_a_channel(self, cf):
        publish(cf, counter=44, channel=None)
        response = cf.release_manifest(Request({'counter': 44}))
        assert status_of(response) == 200
        assert body_of(response)['counter'] == 44
        assert body_of(response)['channel'] is None

    def test_counter_takes_precedence_over_channel(self, cf):
        publish(cf, counter=48, channel='stable')
        publish(cf, counter=44, channel=None)
        returned = body_of(cf.release_manifest(
            Request({'counter': 44, 'channel': 'stable'})))
        assert returned['counter'] == 44

    def test_an_unpublished_counter_is_a_404(self, cf):
        response = cf.release_manifest(Request({'counter': 999}))
        assert status_of(response) == 404
        assert '999' in body_of(response)['error']

    def test_a_string_counter_is_accepted(self, cf):
        publish(cf, counter=44, channel=None)
        assert status_of(cf.release_manifest(Request({'counter': '44'}))) == 200

    @pytest.mark.parametrize('bad', ['abc', [], {}, 'forty-four'])
    def test_a_non_integer_counter_is_a_400(self, cf, bad):
        assert status_of(cf.release_manifest(Request({'counter': bad}))) == 400


class TestHalfPublishedRelease:
    """A promotion mistake should say so, not fail later as a bad signature."""

    def test_a_missing_signature_is_a_500_naming_the_field(self, cf):
        cf.releases.RELEASES['x86'][48] = {
            'manifest_b64': base64.b64encode(signed_manifest()).decode('ascii')}
        cf.releases.CHANNELS['x86']['stable'] = 48
        response = cf.release_manifest(Request({}))
        assert status_of(response) == 500
        assert 'signature' in body_of(response)['error']

    def test_a_missing_manifest_is_a_500_naming_the_field(self, cf):
        cf.releases.RELEASES['x86'][48] = {'signature': 'SIG'}
        cf.releases.CHANNELS['x86']['stable'] = 48
        response = cf.release_manifest(Request({}))
        assert status_of(response) == 500
        assert 'manifest_b64' in body_of(response)['error']

    def test_an_empty_string_counts_as_missing(self, cf):
        cf.releases.RELEASES['x86'][48] = {'manifest_b64': '', 'signature': 'SIG'}
        cf.releases.CHANNELS['x86']['stable'] = 48
        assert status_of(cf.release_manifest(Request({}))) == 500

    def test_a_channel_pointing_at_nothing_is_a_404(self, cf):
        cf.releases.CHANNELS['x86']['stable'] = 48   # never added to RELEASES
        response = cf.release_manifest(Request({}))
        assert status_of(response) == 404
        assert '48' in body_of(response)['error']


class TestHeaders:

    def test_responses_are_json(self, cf):
        publish(cf)
        assert headers_of(cf.release_manifest(Request({})))['Content-Type'] \
            == 'application/json'

    def test_manifests_are_not_cached(self, cf):
        """A cached manifest delays a security release."""
        publish(cf)
        assert headers_of(cf.release_manifest(Request({})))['Cache-Control'] \
            == 'no-store'

    def test_errors_are_json_too(self, cf):
        response = cf.release_manifest(Request({'channel': 'nope'}))
        assert headers_of(response)['Content-Type'] == 'application/json'


class TestEndToEndAgainstVerify:
    """What the device will actually do with the response."""

    def test_the_served_manifest_parses_as_a_manifest(self, cf):
        publish(cf)
        returned = body_of(cf.release_manifest(Request({})))
        parsed = m.loads(base64.b64decode(returned['manifest_b64']))
        assert parsed['release'] == '1.9.1'
        m.validate(parsed)

    def test_the_served_manifest_is_still_canonical(self, cf):
        """So the device can re-derive the same bytes the signer signed."""
        publish(cf)
        returned = body_of(cf.release_manifest(Request({})))
        raw = base64.b64decode(returned['manifest_b64'])
        assert m.canonical_bytes(m.loads(raw)) == raw

    def test_the_envelope_counter_is_not_what_the_device_trusts(self, cf):
        """The envelope counter sits outside the signature and can disagree
        with the manifest - here it does, because the release was published
        under key 48 while the manifest itself says 1. A device that believed
        the envelope would make a rollback decision on an unsigned number, so
        it must read the counter from the verified manifest."""
        publish(cf, counter=48)
        returned = body_of(cf.release_manifest(Request({})))
        parsed = m.loads(base64.b64decode(returned['manifest_b64']))
        assert returned['counter'] == 48
        assert parsed['counter'] == 1
        assert returned['counter'] != parsed['counter']
