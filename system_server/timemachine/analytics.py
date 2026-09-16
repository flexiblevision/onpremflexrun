"""Time machine events -> the prediction sync -> a domain envelope.

Writes a small record into `img_analytics` so the event rides the sync every
other domain uses. The zip itself still goes via `zip_push` to TMEventIngest.
"""
import datetime
import os

from pymongo import MongoClient

MONGODB_HOST = os.environ.get('MONGO_SERVER', '172.17.0.1')
MONGODB_PORT = int(os.environ.get('MONGO_PORT', 27017))

_client = MongoClient(host=MONGODB_HOST, port=MONGODB_PORT,
                      serverSelectionTimeoutMS=5000)
analytics_coll = _client['fvonprem']['img_analytics']

DOMAIN = 'time_machine'

_stats = {'recorded': 0, 'skipped_no_id': 0, 'skipped_no_time': 0, 'failed': 0}


def stats():
    return dict(_stats)


def _ms():
    return int(round(datetime.datetime.now().timestamp() * 1000))


def _iso_from_seconds(value):
    """Epoch seconds -> ISO-8601 UTC. record_start_time is seconds, not ms."""
    return datetime.datetime.fromtimestamp(
        int(value), tz=datetime.timezone.utc).isoformat()


def build_record(event, device_id=None):
    """The analytics record for one pushed event, or None if unattributable."""
    event_id = event.get('id')
    if not event_id:
        _stats['skipped_no_id'] += 1
        return None

    started = event.get('record_start_time')
    if not started:
        _stats['skipped_no_time'] += 1
        return None

    ended = event.get('record_end_time')
    serial = event.get('serial_number')

    record = {
        'id': event_id,
        'domain': DOMAIN,
        'event_ts': _iso_from_seconds(started),
        'end_ts': _iso_from_seconds(ended) if ended else None,
        'serial_number': serial,
        'zip_name': event.get('zip_name'),
        'zip_path': event.get('zip_path'),
        'filepath_mp4': event.get('filepath_mp4'),
        'filepath_webm': event.get('filepath_webm'),
        'camera': event.get('camera'),
        'metadata': {
            'device_id': device_id or event.get('device_id'),
            'workstation': event.get('workstation'),
            'site_id': event.get('site_id'),
        },
        'synced': False,
        'modified': _ms(),
    }
    # No bucket: a recording lands in the device's, resolved cloud-side.
    return {k: v for k, v in record.items() if v is not None}


def record_event(event, device_id=None):
    """Queue one pushed event for the sync. Idempotent on `id`; never raises."""
    try:
        record = build_record(event, device_id)
        if record is None:
            return False
        analytics_coll.update_one({'id': record['id']}, {'$set': record},
                                  upsert=True)
        _stats['recorded'] += 1
        return True
    except Exception as error:
        _stats['failed'] += 1
        print('timemachine analytics record failed for {}: {}'.format(
            event.get('id'), error))
        return False
