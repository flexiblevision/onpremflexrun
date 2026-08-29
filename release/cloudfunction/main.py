"""release_manifest - serve the signed release for a channel.

Deploy (Gen 2, us-central1, flexible-vision-staging):

    gcloud functions deploy release_manifest \\
      --gen2 --runtime python312 --trigger-http --allow-unauthenticated \\
      --entry-point release_manifest --region us-central1 \\
      --source release/cloudfunction

--allow-unauthenticated is required, not optional: functions-proxy is a
transparent pass-through, so without it every device gets a 403 exactly as the
other Gen 2 functions in this project do.

Gen 2 also publishes a *.a.run.app URL on a different IP range. Devices must
keep reaching this through functions-proxy.flexiblevision.com, which is the
single IP customer firewalls allow - never the run.app address.

Reads are unauthenticated on purpose. The manifest is signed, so the transport
is untrusted by design and there is nothing secret in it: an attacker who
intercepts or replaces the response cannot forge a release, and the device's
monotonic counter refuses an older genuinely-signed one. Availability is the
only property this endpoint provides.

Request:   {"channel": "stable"}        or {"counter": 44} for recovery
Response:  {"schema", "channel", "counter", "manifest_b64", "signature"}
"""
import json

import releases

ENVELOPE_SCHEMA = 'flexrun.release.envelope/v1'

_HEADERS = {
    'Content-Type': 'application/json',
    # A cached manifest delays a security release, which is the one case where
    # propagation speed matters.
    'Cache-Control': 'no-store',
}


def _json(payload, status=200):
    return (json.dumps(payload), status, _HEADERS)


def _envelope(counter, channel=None):
    entry = releases.RELEASES.get(counter)
    if not entry:
        return _json({'error': 'release {} is not published'.format(counter)}, 404)

    # Anything missing here is a promotion mistake, and a half-envelope would
    # fail verification on the device with a confusing error. Say so instead.
    for field in ('manifest_b64', 'signature'):
        if not entry.get(field):
            return _json({'error': 'release {} has no {}'.format(counter, field)}, 500)

    return _json({
        'schema': ENVELOPE_SCHEMA,
        'channel': channel,
        'counter': counter,
        # Verbatim. See the note in releases.py.
        'manifest_b64': entry['manifest_b64'],
        'signature': entry['signature'],
    })


def release_manifest(request):
    body = request.get_json(silent=True) or {}

    counter = body.get('counter')
    if counter is not None:
        try:
            counter = int(counter)
        except (TypeError, ValueError):
            return _json({'error': 'counter must be an integer'}, 400)
        return _envelope(counter)

    channel = body.get('channel', 'stable')
    if channel not in releases.CHANNELS:
        return _json({
            'error': "unknown channel '{}'".format(channel),
            'channels': sorted(releases.CHANNELS),
        }, 404)

    promoted = releases.CHANNELS[channel]
    if promoted is None:
        return _json({'error': "no release promoted to channel '{}'".format(channel)}, 404)

    return _envelope(promoted, channel=channel)
