from pymongo import MongoClient, ASCENDING
import datetime
from bson import json_util, ObjectId
import time
import json
import os

client  = MongoClient("172.17.0.1")
tm_db = client['fvonprem']['time_machine']
tm_records_db = client['fvonprem']['event_records']

def get_archive_days():
    tm = tm_db.find_one()
    return int(tm['archive_days'])

def cleanup_timemachine_records():
    """Remove recordings older than the archive window.

    Always one interval behind the moment it runs: archive_days of 1 keeps
    everything from the last 24 hours and removes what is older than that,
    measured from now rather than from the last run - a device that was off
    for a week does not get a week's grace on top.

    record_start_time is epoch SECONDS (visionapi builds its timeframe tics
    from min_s = 60 against the same field), so the arithmetic here is in
    seconds too.
    """
    days   = get_archive_days()
    s_day  = 86400
    time_now = int(datetime.datetime.now().timestamp())
    time_back = time_now - (s_day*days)
    data = tm_records_db.find({"record_start_time": {"$lt": int(time_back) }})
    records = json.loads(json_util.dumps(data))

    num_to_remove = len(records)
    num_removed   = 0
    failed        = []

    for record in records:
        base_path = os.environ['HOME']+'/../home/visioncell'
        paths  = [base_path + record[key]
                  for key in ('filepath_webm', 'filepath_mp4') if record.get(key)]
        errors = []

        for path in paths:
            try:
                os.remove(path)
            except FileNotFoundError:
                # Already gone. The record still has to go with it, or every
                # later pass finds it again and never makes progress.
                pass
            except OSError as error:
                errors.append('{}: {}'.format(path, error))

        if errors:
            failed.extend(errors)
            continue

        num_removed += 1
        tm_records_db.delete_one({'id': record['id']})

    logs = {
        'num_records': num_to_remove,
        'removed': num_removed,
        'failed': failed
    }
    return logs

