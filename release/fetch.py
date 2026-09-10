"""Fetch a signed release manifest from the cloud function.

The transport is untrusted by design. This module does no verification at all -
it returns bytes and a signature, and release/verify.py decides whether to
believe them. Keeping the two apart is what makes the fetch boring: an attacker
who controls the network can withhold a release or replay an old one, and
neither forges one, because the counter and the signature are checked after.

    raw, signature, envelope = fetch_release(arch='x86')
    parsed = verify.verify(raw, arch, high_water, now, public_key_path=...)
"""
import base64
import json

DEFAULT_BASE = 'https://functions-proxy.flexiblevision.com/'
ENDPOINT = 'release_manifest'
TIMEOUT = 30

# Devices reach this only through functions-proxy: it is the single IP customer
# firewalls allow. Gen 2 also publishes a *.a.run.app address on a different
# range, which would work here and fail on a real factory network.
class FetchError(Exception):
    pass


def _post(base, ref, payload, session=None, timeout=TIMEOUT):
    import requests

    http = session or requests
    url = base.rstrip('/') + '/' + ref
    try:
        return http.post(url, json=payload,
                         headers={'Content-Type': 'application/json'},
                         timeout=timeout)
    except Exception as exc:
        raise FetchError('could not reach {}: {}'.format(url, exc))


def fetch_release(arch, channel='stable', counter=None, base=DEFAULT_BASE,
                  session=None, timeout=TIMEOUT):
    """Returns (raw_manifest_bytes, signature, envelope).

    counter asks for one specific release instead of whatever a channel points
    at, which is how a device recovers a release it has run before.
    """
    if not arch:
        raise FetchError('arch is required: a manifest is for one architecture')

    payload = {'arch': arch}
    if counter is not None:
        payload['counter'] = int(counter)
    else:
        payload['channel'] = channel

    response = _post(base, ENDPOINT, payload, session=session, timeout=timeout)
    status = getattr(response, 'status_code', None)

    if status == 404:
        # Nothing promoted yet is the normal state of a channel, not a fault.
        raise FetchError(_detail(response) or 'no release published')
    if status != 200:
        raise FetchError('release endpoint returned HTTP {}{}'.format(
            status, ': ' + _detail(response) if _detail(response) else ''))

    try:
        envelope = response.json()
    except Exception:
        raise FetchError('release endpoint did not return JSON')
    if not isinstance(envelope, dict):
        raise FetchError('release endpoint returned {}, expected an object'
                         .format(type(envelope).__name__))

    for field in ('manifest_b64', 'signature'):
        if not envelope.get(field):
            raise FetchError('response has no {}'.format(field))

    served = envelope.get('arch')
    if served and served != arch:
        # verify() checks this too, against the signed bytes. Catching it here
        # names the endpoint rather than failing later as a signature mismatch.
        raise FetchError('asked for {} and was served {}'.format(arch, served))

    try:
        raw = base64.b64decode(envelope['manifest_b64'], validate=True)
    except Exception as exc:
        raise FetchError('manifest_b64 is not valid base64: {}'.format(exc))
    if not raw:
        raise FetchError('manifest_b64 decoded to nothing')

    return raw, str(envelope['signature']).strip(), envelope


def _detail(response):
    try:
        body = response.json()
    except Exception:
        return ''
    return body.get('error', '') if isinstance(body, dict) else ''


def available(arch, high_water, channel='stable', base=DEFAULT_BASE,
              session=None, timeout=TIMEOUT):
    """What the channel offers, as a summary for the settings screen.

    Never raises for an unreachable endpoint: a device on a factory network is
    offline more often than not, and "could not check" is a state to display,
    not an error to propagate into the UI.
    """
    try:
        raw, signature, envelope = fetch_release(
            arch, channel=channel, base=base, session=session, timeout=timeout)
    except FetchError as exc:
        return {'reachable': False, 'detail': str(exc)}

    try:
        parsed = json.loads(raw.decode('utf-8'))
    except Exception:
        return {'reachable': True, 'detail': 'served an unparsable manifest'}

    counter = parsed.get('counter')
    return {
        'reachable': True,
        'counter': counter,
        'release': parsed.get('release'),
        # Unverified: these bytes have not been checked against a key yet, so
        # this is "what is on offer", not "what will be installed".
        'newer_than_installed': isinstance(counter, int) and counter > high_water,
    }
