"""Which cloud this device talks to, and which release channel it follows.

Resolution order, highest first:

  1. client_mode.master_ip in mongo   cluster mode: the master on the LAN
  2. the cloud_env override in mongo  the runtime switch
  3. $CLOUD_DOMAIN                    what system_setup.sh injected at start
  4. the caller's fallback            settings.config, as its process read it
  5. cloud_domain in ~/fvconfig.json
  6. DEFAULT_CLOUD_DOMAIN

The caller's fallback outranks the file because a caller passing one has
already resolved the config for itself and is not second-guessed; the file is
there for callers that pass nothing.

Mongo is an override rather than the base because it is not there when the
config is first read: system_setup.sh reads fvconfig.json and provisions the
release trust store before the mongo container exists. The file and the
environment stay the bootstrap. The override is what lets a device change
cloud afterwards without recreating every container to re-inject
$CLOUD_DOMAIN.

The override is split by plane, because the two halves carry very different
risk. See DATA_PLANE_KEYS / RELEASE_PLANE_KEYS below.

Reads are cached for CACHE_TTL seconds: get_cloud_domain() sits on the model
download and device flow paths, and a find_one per call would put a round trip
in front of every sync. A mongo that has gone away keeps serving the last
value rather than falling back, so a blip cannot silently repoint a device
mid-run.

The override document is a cross-service contract, not private to this repo.
The waveform site service reads the same record directly - see
audio_anomaly/cloud.py in the FVKWS project - so OVERRIDE_TYPE and the shape
`{'type': OVERRIDE_TYPE, 'config': {...}}` cannot be renamed here alone.
"""
import datetime
import json
import os
import sys
import time

DEFAULT_CLOUD_DOMAIN = "https://v1.cloud.flexiblevision.com"
DEFAULT_FUNCTIONS_BASE = "https://functions-proxy.flexiblevision.com/"
DEFAULT_CHANNEL = 'stable'
DEFAULT_STABLE_REF = 'latest_stable_version'

CHANNELS = ('stable', 'beta')

OVERRIDE_TYPE = 'cloud_env'

# The data plane: where clips, projects and models go. Switchable on any site,
# including production. Repointing it moves traffic and nothing else, and a
# site aimed at the wrong cloud fails visibly on its next call.
DATA_PLANE_KEYS = ('cloud_domain', 'gcp_functions_domain')

# The release plane: which channel and which version endpoint this device
# takes SIGNED RELEASES from. Honoured only where fvconfig says release_track
# is 'dev', or allow_runtime_override is true - mongo on 172.17.0.1 takes no
# credentials, and a write there must not be able to walk a customer device
# onto pre-release software.
RELEASE_PLANE_KEYS = ('latest_stable_ref', 'release_channel')

OVERRIDE_KEYS = DATA_PLANE_KEYS + RELEASE_PLANE_KEYS

CACHE_TTL = 30

_utils_coll = None
# 'at' is None until the first read. Not 0.0: time.monotonic() is time since
# boot, so a container starting seconds after boot would read 0.0 as a fresh
# cache and ignore the override for the rest of the window.
_override_cache = {'at': None, 'value': {}}


class CloudEnvError(Exception):
    """Raised when an override cannot be written, or is not allowed here."""


def reset_cache():
    _override_cache['at'] = None
    _override_cache['value'] = {}


def _now_iso():
    return datetime.datetime.utcnow().replace(microsecond=0).strftime(
        '%Y-%m-%dT%H:%M:%SZ')


def _site_config():
    try:
        with open(os.environ['HOME'] + '/fvconfig.json') as f:
            return json.load(f)
    except Exception:
        return {}


def _local_config():
    cfg = _site_config()
    return cfg if cfg.get('environ') == 'local' else None


def _utils_collection():
    global _utils_coll
    if _utils_coll is None:
        from pymongo import MongoClient
        client = MongoClient(
            os.environ.get('MONGO_SERVER', '172.17.0.1'),
            int(os.environ.get('MONGO_PORT', 27017)),
            serverSelectionTimeoutMS=2000,
        )
        _utils_coll = client['fvonprem']['utils']
    return _utils_coll


def _master_ip_from_db():
    # In local/client mode the master address lives in the client_mode utility
    # record (the same source cameras/prediction/system use).
    try:
        rec = _utils_collection().find_one({'type': 'client_mode'})
        ip = (rec or {}).get('config', {}).get('master_ip')
        if ip:
            return ip if ip.startswith(('http://', 'https://')) else 'http://' + ip
    except Exception:
        pass
    return None


def _clean(raw):
    """The override keys that are actually usable, and nothing else.

    Anything unrecognised, blank or not a string is dropped rather than
    passed on: a half-typed record in the database must not become a URL a
    worker then fails against.
    """
    out = {}
    for key in OVERRIDE_KEYS:
        value = raw.get(key) if hasattr(raw, 'get') else None
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    if out.get('release_channel') not in CHANNELS:
        out.pop('release_channel', None)
    return out


def release_override_allowed(cfg=None):
    """Whether the release-plane keys are honoured on this device."""
    cfg = _site_config() if cfg is None else cfg
    return (cfg.get('release_track') == 'dev'
            or cfg.get('allow_runtime_override') is True)


def _cached_override():
    """The stored record, both planes, cached - never raises."""
    now = time.monotonic()
    at = _override_cache['at']
    if at is not None and now - at < CACHE_TTL:
        return _override_cache['value']

    try:
        found = _utils_collection().find_one({'type': OVERRIDE_TYPE}, {'_id': 0})
    except Exception:
        # Keep serving the last good answer, and back off for the window.
        # Falling back to the file would repoint a device at a different cloud
        # in the middle of a sync because mongo blipped; retrying per call
        # would put the 2s connect timeout in front of every download.
        _override_cache['at'] = now
        return _override_cache['value']

    _override_cache['value'] = _clean((found or {}).get('config') or {})
    _override_cache['at'] = now
    return _override_cache['value']


def read_override(cfg=None):
    """The override in force here, or {} - never raises.

    Off the dev track the release-plane keys are dropped rather than the whole
    record: a production site can still be repointed at another cloud, it just
    cannot be moved onto another release channel.
    """
    cfg = _site_config() if cfg is None else cfg
    stored = _cached_override()
    if release_override_allowed(cfg):
        return stored
    return {key: value for key, value in stored.items()
            if key in DATA_PLANE_KEYS}


def _cloud_base(fallback=None):
    """Which cloud this device talks to, or None if nothing says.

    `fallback` is the caller's own view of the config - settings.config, as
    read when its process started. It sits above the file because a caller
    that passes one has already resolved the config for itself, and below the
    environment and the override because those two are what a switch moves.
    """
    cfg = _site_config()
    if cfg.get('environ') == 'local':
        # Cluster mode: the master on the LAN, whose address the client_mode
        # record tracks. Not a cloud, and not what the override moves.
        return _master_ip_from_db() or cfg.get('cloud_domain')
    return (read_override(cfg).get('cloud_domain')
            or os.environ.get('CLOUD_DOMAIN')
            or fallback
            or cfg.get('cloud_domain'))


def get_cloud_domain(fallback=None):
    return _cloud_base(fallback) or DEFAULT_CLOUD_DOMAIN


def get_cloud_functions_base(fallback=None):
    cfg = _site_config()
    if cfg.get('environ') == 'local':
        # A cluster site has no functions endpoint of its own; the master
        # proxies them.
        base = _cloud_base()
        return (base.rstrip('/') + '/api/capture/functions/' if base
                else fallback or DEFAULT_FUNCTIONS_BASE)
    return (read_override(cfg).get('gcp_functions_domain')
            or os.environ.get('GCP_FUNCTIONS_DOMAIN')
            or fallback
            or cfg.get('gcp_functions_domain')
            or DEFAULT_FUNCTIONS_BASE)


def get_release_channel(fallback=DEFAULT_CHANNEL):
    """Which release channel this device follows.

    Anything unrecognised is stable, at both layers: a device must never end
    up on beta because a record or a config was malformed.
    """
    channel = read_override().get('release_channel') or fallback
    return channel if channel in CHANNELS else DEFAULT_CHANNEL


def get_latest_stable_ref(fallback=DEFAULT_STABLE_REF):
    return read_override().get('latest_stable_ref') or fallback


# --- writing the override ---------------------------------------------------

def _stored(collection=None):
    coll = collection or _utils_collection()
    found = coll.find_one({'type': OVERRIDE_TYPE}, {'_id': 0})
    return _clean((found or {}).get('config') or {})


def require_override_allowed(values=None):
    """Raise unless the keys about to be written would be honoured here.

    `values` is the keys being written; omitted means all of them, which is
    what the CLI's track switch does. Callers with work to do before the
    write - resolving a track, which imports setup.management - check this
    first, so a command that is going to be refused does nothing on the way.
    """
    keys = OVERRIDE_KEYS if values is None else tuple(values)
    refused = sorted(key for key in keys if key in RELEASE_PLANE_KEYS)
    if not refused or release_override_allowed():
        return

    raise CloudEnvError(
        "{} decide{} where this device takes signed releases from, and it is "
        "on the '{}' release track where that stays pinned to "
        '~/fvconfig.json - add "allow_runtime_override": true there to opt it '
        'in. {} can be set on any device.'.format(
            ' and '.join(refused), '' if len(refused) > 1 else 's',
            _site_config().get('release_track', 'prod'),
            ' and '.join(DATA_PLANE_KEYS)))


def set_override(values, collection=None):
    """Point this device at a different cloud or channel, live.

    Refuses keys this device would not honour, rather than writing a record
    that changes nothing and letting somebody believe the device moved. Mongo
    failures propagate for the same reason.
    """
    require_override_allowed(values)

    unknown = sorted(set(values) - set(OVERRIDE_KEYS))
    if unknown:
        raise CloudEnvError('unknown override key(s): {} - expected {}'.format(
            ', '.join(unknown), ', '.join(OVERRIDE_KEYS)))

    clean = _clean(values)
    unusable = sorted(set(values) - set(clean))
    if unusable:
        raise CloudEnvError('unusable value for: {}{}'.format(
            ', '.join(unusable),
            ' (release_channel must be one of {})'.format(
                ', '.join(CHANNELS)) if 'release_channel' in unusable else ''))

    for key in ('cloud_domain', 'gcp_functions_domain'):
        if key in clean and not clean[key].startswith(('http://', 'https://')):
            raise CloudEnvError('{} needs a scheme, got {!r}'.format(
                key, clean[key]))

    merged = _stored(collection)
    merged.update(clean)

    coll = collection or _utils_collection()
    coll.update_one({'type': OVERRIDE_TYPE},
                    {'$set': {'config': merged, 'updated_at': _now_iso()}},
                    upsert=True)
    reset_cache()
    return merged


def clear_override(collection=None):
    """Drop the override and go back to what fvconfig.json says."""
    coll = collection or _utils_collection()
    coll.delete_one({'type': OVERRIDE_TYPE})
    reset_cache()


def track_override(track):
    """The full set of values a release track implies.

    setup.management is imported here and not at module scope because
    importing it runs generate_environment_config(), and reading the cloud
    domain must not create a config as a side effect.
    """
    from setup.management import track_settings
    return track_settings(track, _site_config().get('environ', 'cloud'))


# --- CLI --------------------------------------------------------------------

USAGE = ('usage: cloud_env.py show\n'
         '       cloud_env.py track <prod|dev>\n'
         '       cloud_env.py set KEY=VALUE [KEY=VALUE ...]\n'
         '       cloud_env.py clear\n'
         '\n'
         'keys, any device:   ' + ', '.join(DATA_PLANE_KEYS) + '\n'
         'keys, dev track:    ' + ', '.join(RELEASE_PLANE_KEYS) + '\n')


def _print_state():
    cfg = _site_config()
    print('release_track:        {}'.format(cfg.get('release_track', 'prod')))
    print('release override:     {}'.format(
        'honoured' if release_override_allowed(cfg) else 'not honoured here'))
    # show has to work on a device whose mongo is down - that is one of the
    # things you run it to find out. Only the write paths let mongo errors out.
    try:
        print('stored override:      {}'.format(_stored() or '(none)'))
    except Exception as exc:
        print('stored override:      (unreadable: {})'.format(exc))
    print('in force:             {}'.format(read_override(cfg) or '(none)'))
    print('cloud_domain:         {}'.format(get_cloud_domain()))
    print('cloud functions base: {}'.format(get_cloud_functions_base()))
    print('release_channel:      {}'.format(get_release_channel()))
    print('latest_stable_ref:    {}'.format(get_latest_stable_ref()))


def main(argv):
    if not argv or argv[0] in ('-h', '--help'):
        print(USAGE)
        return 0 if argv else 2

    command, rest = argv[0], argv[1:]
    try:
        if command == 'show':
            _print_state()
        elif command == 'clear':
            clear_override()
            print('override cleared - back to ~/fvconfig.json')
        elif command == 'track':
            if len(rest) != 1:
                print(USAGE)
                return 2
            require_override_allowed()
            print('override set: {}'.format(set_override(track_override(rest[0]))))
        elif command == 'set':
            if not rest or any('=' not in pair for pair in rest):
                print(USAGE)
                return 2
            values = dict(pair.split('=', 1) for pair in rest)
            print('override set: {}'.format(set_override(values)))
        else:
            print(USAGE)
            return 2
    except (CloudEnvError, ValueError) as exc:
        print('cloud_env: {}'.format(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
