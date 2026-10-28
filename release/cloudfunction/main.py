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

This source also carries latest_stable_version_dev - the same shape as the
legacy latest_stable_version endpoint, but answering from the beta channel.
Deploy it from here too:

    gcloud functions deploy latest_stable_version_dev \\
      --gen2 --runtime python312 --trigger-http --allow-unauthenticated \\
      --entry-point latest_stable_version_dev --region us-central1 \\
      --source release/cloudfunction
"""
import base64
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


def _envelope(arch, counter, channel=None):
    entry = (releases.RELEASES.get(arch) or {}).get(counter)
    if not entry:
        return _json({'error': 'release {} is not published for {}'.format(
            counter, arch)}, 404)

    # Anything missing here is a promotion mistake, and a half-envelope would
    # fail verification on the device with a confusing error. Say so instead.
    for field in ('manifest_b64', 'signature'):
        if not entry.get(field):
            return _json({'error': 'release {} has no {}'.format(counter, field)}, 500)

    return _json({
        'schema': ENVELOPE_SCHEMA,
        'arch': arch,
        'channel': channel,
        'counter': counter,
        # Verbatim. See the note in releases.py.
        'manifest_b64': entry['manifest_b64'],
        'signature': entry['signature'],
    })


def release_manifest(request):
    body = request.get_json(silent=True) or {}

    # Required, never defaulted. Guessing x86 would hand an arm device a
    # manifest full of images that do not exist for it, and the counter it
    # carries would be from a sequence that device does not follow.
    arch = body.get('arch')
    if arch not in releases.CHANNELS:
        return _json({
            'error': "unknown or missing arch {!r}".format(arch),
            'arches': sorted(releases.CHANNELS),
        }, 400)

    counter = body.get('counter')
    if counter is not None:
        try:
            counter = int(counter)
        except (TypeError, ValueError):
            return _json({'error': 'counter must be an integer'}, 400)
        return _envelope(arch, counter)

    channel = body.get('channel', 'stable')
    if channel not in releases.CHANNELS[arch]:
        return _json({
            'error': "unknown channel '{}'".format(channel),
            'channels': sorted(releases.CHANNELS[arch]),
        }, 404)

    promoted = releases.CHANNELS[arch][channel]
    if promoted is None:
        return _json({'error': "no release promoted to '{}' for {}".format(
            channel, arch)}, 404)

    return _envelope(arch, promoted, channel=channel)


# --- latest_stable_version_dev ---------------------------------------------
# A device on the dev track sets latest_stable_ref to latest_stable_version_dev
# and follows beta. This answers in the legacy endpoint's shape - a bare version
# string as text/plain - so version_check.py needs no dev-specific code, and a
# dev device that has not yet moved to the signed-manifest path still gets beta
# versions rather than the fleet's.
#
# This is the one place a manifest is decoded, and it is read-only: the
# signature-covered bytes in releases.json are never rewritten from what is
# parsed here.
_TEXT_HEADERS = {'Content-Type': 'text/plain', 'Cache-Control': 'no-store'}


def _beta_images(arch):
    counter = (releases.CHANNELS.get(arch) or {}).get('beta')
    if counter is None:
        return None, 'nothing is promoted to beta for {}'.format(arch)
    entry = (releases.RELEASES.get(arch) or {}).get(counter)
    if not entry or not entry.get('manifest_b64'):
        return None, 'beta points at release {}, which is not published'.format(
            counter)
    try:
        manifest = json.loads(base64.b64decode(entry['manifest_b64']))
    except (ValueError, TypeError):
        return None, 'release {} has an unreadable manifest'.format(counter)
    return manifest.get('images', {}).get(arch, {}), None


def latest_stable_version_dev(request):
    body = request.get_json(silent=True) or {}
    arch = body.get('arch')
    image = body.get('image')

    if arch not in releases.CHANNELS:
        return _json({'error': 'unknown or missing arch {!r}'.format(arch),
                      'arches': sorted(releases.CHANNELS)}, 400)
    if not image:
        return _json({'error': 'missing image'}, 400)

    images, problem = _beta_images(arch)
    if problem:
        # 404, not a fallback to stable: a dev device asked what beta is, and
        # answering with the fleet's version would silently put it back on the
        # track it was deliberately taken off.
        return _json({'error': problem}, 404)

    component = images.get(image)
    if not component or not component.get('tag'):
        return _json({'error': "beta has no version for '{}' on {}".format(
            image, arch)}, 404)

    return (component['tag'], 200, _TEXT_HEADERS)
