"""Report enabled domains to the cloud; cache the place it returns.

Domains are a device fact, place is a cloud fact. See DEVICE_PLACEMENT.md.
"""
import os
import sys
import time

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


def cached_place():
    """Where the cloud last said this device is, or None."""
    doc = utils_db.find_one({'type': PLACE_TYPE}, {'_id': 0})
    return (doc or {}).get('place')


def push_device_identity(cloud_domain, access_token):
    """PUT the domains, cache the place. Never raises."""
    try:
        dev_id = device_id()
        domains = reported_domains()
    except Exception as error:
        print('device identity lookup failed: {}'.format(error))
        return None
    if not dev_id:
        return None

    url = '{}/api/assembly/stations/device/{}'.format(
        cloud_domain.rstrip('/'), dev_id)
    try:
        res = requests.put(url, json={'domains': domains},
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
