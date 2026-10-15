"""Report enabled domains and the timezone to the cloud; cache the place it returns.

Domains and timezone are device facts, place is a cloud fact. See DEVICE_PLACEMENT.md.
The device timezone is the one source for how the site's data is presented, in
cloud as on-prem, so the cloud never sets its own.
"""
import os
import sys
import time
from zoneinfo import ZoneInfo

import requests
from pymongo import MongoClient

settings_path = os.environ['HOME'] + '/flex-run'
sys.path.append(settings_path)

from addons import state as addon_state

MONGODB_HOST = os.environ.get('MONGO_SERVER', '172.17.0.1')
MONGODB_PORT = int(os.environ.get('MONGO_PORT', 27017))

_client = MongoClient(host=MONGODB_HOST, port=MONGODB_PORT,
                      serverSelectionTimeoutMS=5000)
utils_db = _client['fvonprem']['utils']

PLACE_TYPE = 'device_place'
TIMEOUT = 20

# addons/catalog -> analytics domain. ocr is a model_type inside inspection;
# client_mode and ftp are not domains. inspection is baseline, never reported.
ADDON_DOMAINS = {
    'assembly': 'assembly',
    'anomaly_visual': 'anomaly',
    'anomaly_audio': 'waveform',
    'timemachine': 'time_machine',
}


def device_id():
    ref = utils_db.find_one({'type': 'device_id'})
    return ref['id'] if ref else None


def reported_domains():
    """Add-on domains this device has enabled."""
    return sorted({ADDON_DOMAINS[name] for name in addon_state.enabled()
                   if name in ADDON_DOMAINS})


def device_timezone():
    """The zone on-prem presents data in, resolved as the webserver's
    utils/device_time.name() does: the system setting, the legacy kiosk zone,
    then the host's. None when none is a known zone."""
    candidates = []
    try:
        rec = utils_db.find_one({'type': 'timezone'}) or {}
        candidates.append(rec.get('name'))
        kiosk = _client['fvonprem']['kiosk_site_config'].find_one({'_id': 'site'}) or {}
        candidates.append(((kiosk.get('cfg') or {}).get('localization') or {}).get('timezone'))
    except Exception as error:
        print('device timezone lookup failed: {}'.format(error))
    try:
        with open('/etc/timezone') as fh:
            candidates.append(fh.read().strip())
    except Exception:
        pass
    name = next((c for c in candidates if c), None)
    if not name:
        return None
    try:
        ZoneInfo(name)
    except Exception:
        # The cloud refuses an unknown zone, and with it the domain report
        print('device timezone {!r} is not a known zone; not reported'.format(name))
        return None
    return name


def cached_place():
    """Where the cloud last said this device is, or None."""
    doc = utils_db.find_one({'type': PLACE_TYPE}, {'_id': 0})
    return (doc or {}).get('place')


def push_device_identity(cloud_domain, access_token):
    """PUT the domains and timezone, cache the place. Never raises."""
    try:
        dev_id = device_id()
        domains = reported_domains()
        tz = device_timezone()
    except Exception as error:
        print('device identity lookup failed: {}'.format(error))
        return None
    if not dev_id:
        return None

    url = '{}/api/assembly/stations/device/{}'.format(
        cloud_domain.rstrip('/'), dev_id)
    try:
        payload = {'domains': domains}
        if tz:
            payload['timezone'] = tz
        res = requests.put(url, json=payload,
                           headers={'Authorization': 'Bearer ' + access_token},
                           timeout=TIMEOUT)
        res.raise_for_status()
        body = res.json()
    except Exception as error:
        # A stale cached place beats none: it is configuration, not telemetry.
        print('device identity sync failed: {}'.format(error))
        return None

    utils_db.update_one(
        {'type': PLACE_TYPE},
        {'$set': {'place': body.get('place'),
                  'domains': body.get('domains') or [],
                  'unavailable_domains': body.get('unavailable_domains') or [],
                  'reason': body.get('reason'),
                  'synced_at': int(time.time())}},
        upsert=True)
    return body.get('place')
