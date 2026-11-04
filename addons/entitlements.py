"""Is this device licensed for this addon?

One descriptor field, one function, applied identically to every enterprise
addon. The check it replaces - validate_account() in
system_server/timemachine/installer.py - answers "licensed" to a cloud that said
no, to a cloud that said nothing, and to no cloud at all.

Three sources, in order:

  claims   a namespaced custom claim on the token auth.requires_auth has
           already verified against the JWKS. Signed, needs no network, and
           survives an offline device - which is why it is preferred. An absent
           claim is not a denial: during rollout no token carries one yet.
  cache    a previously confirmed grant, honoured through a grace window.
  cloud    validate_service, which revokes faster than a token lifetime.

ENFORCED is False: check() returns the honest answer and callers record it, but
nothing is blocked. Flipping it is the whole of turning enforcement on.
"""
import datetime
import json
import os

import requests

ENFORCED = False

GRACE_DAYS = 30
TIMEOUT = 10

CACHE_PATH = os.environ.get(
    'FLEXRUN_ENTITLEMENT_CACHE', '/var/lib/flex-run/entitlements.json')

# Auth0 drops non-namespaced custom claims, so the namespace is required.
CLAIMS_NAMESPACE = 'https://flexiblevision.com/'
ENTITLEMENTS_CLAIM = CLAIMS_NAMESPACE + 'entitlements'
ORG_CLAIM = CLAIMS_NAMESPACE + 'org_id'

CLAIM = 'claim'
CONFIRMED = 'confirmed'
INCLUDED = 'included'
CACHED = 'cached'
DENIED = 'denied'
DENIED_CLAIM = 'denied_claim'
UNREACHABLE = 'unreachable'
EXPIRED = 'expired'
NO_TOKEN = 'no_token'


class Grant:
    def __init__(self, allowed, reason, checked_at=None, expires_at=None):
        self.allowed = allowed
        self.reason = reason
        self.checked_at = checked_at
        self.expires_at = expires_at

    @property
    def blocking(self):
        """Would this stop an enable, if enforcement were on?"""
        return ENFORCED and not self.allowed

    def as_dict(self):
        return {
            'allowed': self.allowed,
            'reason': self.reason,
            'enforced': ENFORCED,
            'expires_at': self.expires_at,
        }

    def __repr__(self):
        return '<Grant {} {}>'.format(self.allowed, self.reason)


def _now():
    return datetime.datetime.utcnow()


def _iso(moment):
    return moment.replace(microsecond=0).strftime('%Y-%m-%dT%H:%M:%SZ')


def _parse(stamp):
    try:
        return datetime.datetime.strptime(stamp, '%Y-%m-%dT%H:%M:%SZ')
    except (TypeError, ValueError):
        return None


def _cloud_domain():
    import settings
    from cloud_env import get_cloud_domain

    configured = settings.config.get(
        'cloud_domain', 'https://clouddeploy.api.flexiblevision.com')
    return get_cloud_domain(configured)


def load_cache():
    try:
        with open(CACHE_PATH) as handle:
            cache = json.load(handle)
        return cache if isinstance(cache, dict) else {}
    except (IOError, OSError, ValueError):
        return {}


def save_cache(cache):
    try:
        directory = os.path.dirname(CACHE_PATH)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(CACHE_PATH, 'w') as handle:
            json.dump(cache, handle, indent=2, sort_keys=True)
        return True
    except (IOError, OSError) as error:
        print('could not cache entitlements: {}'.format(error))
        return False


def _remember(service, allowed):
    cache = load_cache()
    cache[service] = {
        'allowed': bool(allowed),
        'checked_at': _iso(_now()),
        'expires_at': _iso(_now() + datetime.timedelta(days=GRACE_DAYS)),
    }
    save_cache(cache)
    return cache[service]


def _from_cache(service):
    entry = load_cache().get(service)
    if not entry:
        return None

    expires_at = _parse(entry.get('expires_at'))
    if expires_at is None or expires_at < _now():
        return Grant(False, EXPIRED, entry.get('checked_at'), entry.get('expires_at'))

    if not entry.get('allowed'):
        return Grant(False, DENIED, entry.get('checked_at'), entry.get('expires_at'))

    return Grant(True, CACHED, entry.get('checked_at'), entry.get('expires_at'))


def _ask_cloud(service, access_token):
    """True, False, or None for "could not tell"."""
    url = _cloud_domain() + '/api/capture/auth/validate_service'
    try:
        response = requests.post(
            url,
            headers={'Authorization': 'Bearer ' + access_token},
            json={'service': service},
            timeout=TIMEOUT)
    except Exception as error:
        print('entitlement check for {} could not reach the cloud: {}'
              .format(service, error))
        return None

    # A 401/403 is the cloud declining to answer for this token, not a verdict.
    if response.status_code != 200:
        print('entitlement check for {} returned HTTP {}'
              .format(service, response.status_code))
        return None

    try:
        body = response.json()
    except ValueError:
        return None

    if isinstance(body, bool):
        return body
    if isinstance(body, dict):
        for key in ('valid', 'allowed', 'authorized', 'entitled'):
            if key in body:
                return bool(body[key])
    return None


def from_claims(service, claims):
    """A verdict from the verified token, or None if it carries no claim.

    Absent is not denied: until every token carries the claim, treating a
    missing one as a refusal would lock out every device on the fleet.
    """
    if not isinstance(claims, dict):
        return None

    granted = claims.get(ENTITLEMENTS_CLAIM)
    if granted is None:
        return None
    if isinstance(granted, str):
        granted = [granted]
    if not isinstance(granted, (list, tuple, set)):
        return None

    if service in granted:
        return Grant(True, CLAIM)
    return Grant(False, DENIED_CLAIM)


def check(addon, access_token=None, claims=None):
    """The licence position for one addon descriptor."""
    if addon.get('tier') != 'enterprise':
        return Grant(True, INCLUDED)

    service = addon.get('entitlement')
    if not service:
        return Grant(False, DENIED)

    from_token = from_claims(service, claims)
    if from_token is not None:
        return from_token

    if not access_token:
        cached = _from_cache(service)
        return cached if cached else Grant(False, NO_TOKEN)

    verdict = _ask_cloud(service, access_token)

    if verdict is None:
        cached = _from_cache(service)
        return cached if cached else Grant(False, UNREACHABLE)

    entry = _remember(service, verdict)
    if not verdict:
        return Grant(False, DENIED, entry['checked_at'], entry['expires_at'])
    return Grant(True, CONFIRMED, entry['checked_at'], entry['expires_at'])
