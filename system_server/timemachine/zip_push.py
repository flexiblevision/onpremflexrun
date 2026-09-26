"""Time machine clips -> the device's bucket -> the analytics spine.

The waveform path (FVKWS audio_anomaly/cloud.py): mint signed PUT links from
/api/capture/devices/<device>/upload_links (kind time_machine, which lands under
tm/<device>/ in the device's own bucket), PUT each mp4, then queue the analytics
record naming that bucket and object so SiteViewer can open it.

The zip to TMEventIngest is gone. The module keeps its name because rq jobs
already queued name push_event_records by import path.
"""
import os
from datetime import datetime

import requests
from pymongo import MongoClient

from timemachine import analytics

client            = MongoClient("172.17.0.1")
tm_records_db     = client["fvonprem"]["event_records"]
utils_db          = client["fvonprem"]["utils"]
dev_ref           = utils_db.find_one({'type':'device_id'})
DEV_ID            =  None if not dev_ref else dev_ref['id']

UPLOAD_KIND = 'time_machine'
CONTENT_TYPE = 'video/mp4'
# the cloud mints at most this many links per request (devices.py MAX_UPLOAD_LINKS)
LINK_BATCH = 10
# eventor writes /Videos/... inside its container, mounted from here
HOST_ROOT = '/home/visioncell'
# (connect, per-socket-op): 30s failed every write of the old 80-120MB zips on a
# line uplink, and a 2 minute clip can still be that large
PUSH_TIMEOUT = (15, 300)
MINT_TIMEOUT = 20


def mark_as_processed(event, upload):
    tm_records_db.update_one({'id': event['id']}, {'$set': {
        'processed': True, 'processed_time': datetime.now().timestamp(),
        'cloud_bucket': upload['bucket'], 'cloud_path': upload['path']}})
    # Only now is the recording known to be in the cloud.
    analytics.record_event(event, device_id=DEV_ID, upload=upload)

def mark_as_dequeued(events):
    for event in events:
        tm_records_db.update_one({'id': event['id']}, {'$set': {'queued': False}})

def mark_as_unuploadable(event, reason):
    # processed so it stops being retried every sync; the reason says why
    print('time machine clip %s not uploaded: %s' % (event.get('id'), reason))
    tm_records_db.update_one({'id': event['id']}, {'$set': {
        'processed': True, 'processed_time': datetime.now().timestamp(), 'push_error': reason}})

def get_unprocessed_events():
    event_records = tm_records_db.find({'processed': False, "$or":[ {'queued': { '$exists': 0 }}, {"queued": False}], 'storage_type': 'zip_push'})
    events = []
    for i in event_records:
        del i['_id']
        i['queued'] = True
        print(i)
        tm_records_db.update_one({'id': i['id']}, {'$set': {'queued': True, 'queue_time': datetime.now().timestamp()}})
        events.append(i)

    return {'count': len(events), 'events': events}

def local_path(event):
    path = event.get('filepath_mp4')
    return HOST_ROOT + path if path else None

def mint_links(cloud_domain, token, names):
    """{name: url}, bucket and object prefix for one batch; raises on failure."""
    url = '{}/api/capture/devices/{}/upload_links'.format(cloud_domain.rstrip('/'), DEV_ID)
    res = requests.post(url, json={'kind': UPLOAD_KIND, 'names': names, 'content_type': CONTENT_TYPE},
                        headers={'Authorization': 'Bearer ' + token}, timeout=MINT_TIMEOUT)
    if res.status_code != 200:
        raise RuntimeError('upload link mint failed: %s %s' % (res.status_code, res.text[:200]))
    body = res.json() or {}
    return body.get('links') or {}, body.get('bucket'), body.get('prefix')

def put_clip(signed_url, path):
    # no auth header: the signature is the authorisation, and the site token has
    # no business going to the storage host
    with open(path, 'rb') as handle:
        res = requests.put(signed_url, data=handle, headers={'Content-Type': CONTENT_TYPE},
                           timeout=PUSH_TIMEOUT)
    if res.status_code >= 400:
        raise RuntimeError('upload failed: %s %s' % (res.status_code, res.text[:200]))

def push_event_records(cloud_domain, id_token, event_records):
    """Upload each clip's mp4 to the device's bucket and queue its analytics record."""
    if not DEV_ID:
        print('time machine push skipped: no device_id in fvonprem.utils')
        mark_as_dequeued(event_records['events'])
        return True

    ready = []
    for event in event_records['events']:
        path = local_path(event)
        if not path or not os.path.isfile(path):
            mark_as_unuploadable(event, 'no mp4 on disk (%s)' % path)
        else:
            ready.append((event, path))

    for start in range(0, len(ready), LINK_BATCH):
        batch = ready[start:start + LINK_BATCH]
        try:
            links, bucket, prefix = mint_links(
                cloud_domain, id_token, [os.path.basename(p) for _, p in batch])
        except Exception as error:
            print(error, ' ERROR MINTING TIME MACHINE UPLOAD LINKS')
            mark_as_dequeued([e for e, _ in batch])
            continue

        for event, path in batch:
            name = os.path.basename(path)
            try:
                if name not in links:
                    raise RuntimeError('no upload link returned for ' + name)
                put_clip(links[name], path)
                mark_as_processed(event, {'bucket': bucket, 'path': '%s/%s' % (prefix, name)})
            except Exception as error:
                print(error, ' ERROR UPLOADING TIME MACHINE CLIP')
                mark_as_dequeued([event])

    return True
