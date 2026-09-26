"""What this device has been told to run.

Enabled state is recorded intent, kept apart from the health probe that stands
in for it today - conflating them means a crashed addon reads as disabled and
the UI offers to enable it.

Intent is also the input three things need and cannot currently get: the
enabled_features argument to release/manifest.py:applicable() (via
enabled_components(), not enabled() - see there), the list of containers the
upgrade path should redeploy at the pinned digest, and the set of licences to
re-check.

Mongo rather than fvconfig.json, which generate_environment_config() rewrites
wholesale. Reads tolerate an unreachable database by reporting nothing enabled.
"""
import datetime
import os

COLLECTION = 'addons'
DATABASE = 'fvonprem'

MONGO_TIMEOUT_MS = 5000

_client = None


def _records():
    global _client
    if _client is None:
        from pymongo import MongoClient
        _client = MongoClient(
            os.environ.get('MONGO_SERVER', '172.17.0.1'),
            int(os.environ.get('MONGO_PORT', 27017)),
            serverSelectionTimeoutMS=MONGO_TIMEOUT_MS)
    return _client[DATABASE][COLLECTION]


def _now():
    return datetime.datetime.utcnow()


def record(name):
    try:
        return _records().find_one({'name': name}, {'_id': False})
    except Exception as error:
        print('addon state lookup failed for {}: {}'.format(name, error))
        return None


def is_enabled(name):
    entry = record(name)
    return bool(entry and entry.get('enabled'))


def enabled():
    """Names of every addon this device has been told to run."""
    try:
        cursor = _records().find({'enabled': True}, {'_id': False, 'name': True})
        return sorted(entry['name'] for entry in cursor)
    except Exception as error:
        print('addon state listing failed: {}'.format(error))
        return []


def enabled_components():
    """
    Release components for the addons this device has enabled.

    This, not enabled(), is what release/manifest.py:applicable() wants.
    applicable() matches its enabled_features against manifest component keys,
    but an addon's name and its component are different strings and NAME_RE
    forbids the hyphen every component uses:

        anomaly_audio  -> audio-anomaly
        anomaly_visual -> anomaly-server
        assembly       -> assembly-client
        ocr            -> ocr             (equal only by coincidence)

    Passing names straight through would raise "release does not pin enabled
    feature(s)" on any device with an addon enabled, and no upgrade would apply.

    An enabled addon with no catalog entry is dropped rather than reported: it
    is a stale mongo record for an addon this build no longer ships, and it
    should not be able to block every upgrade.
    """
    from . import registry

    mapping = registry.components()
    return sorted({mapping[name] for name in enabled() if name in mapping})


def all_records():
    try:
        return {entry['name']: entry
                for entry in _records().find({}, {'_id': False})}
    except Exception as error:
        print('addon state listing failed: {}'.format(error))
        return {}


def mark_enabled(name, image=None, by=None, release=None):
    return _write(name, {
        'enabled': True,
        'enabled_at': _now(),
        'enabled_by': by,
        'image': image,
        'release': release,
        'last_error': None,
    })


def mark_disabled(name, by=None):
    return _write(name, {
        'enabled': False,
        'disabled_at': _now(),
        'disabled_by': by,
    })


def mark_failed(name, error):
    # Records why, so "never turned on" and "turned on and the deploy failed"
    # stop looking identical.
    return _write(name, {
        'enabled': False,
        'failed_at': _now(),
        'last_error': str(error)[:500],
    })


def _write(name, fields):
    fields = dict(fields, name=name)
    try:
        _records().update_one({'name': name}, {'$set': fields}, upsert=True)
        return True
    except Exception as error:
        print('addon state write failed for {}: {}'.format(name, error))
        return False
