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
    days   = get_archive_days()
    s_day  = 86400
    time_now = int(datetime.datetime.now().timestamp())
    time_back = time_now - (ms_day*days)
    data = tm_records_db.find({"record_start_time": {"$lt": int(time_back) }})
    records = json.loads(json_util.dumps(data))

    num_to_remove = 0
    num_removed   = 0
    failed        = []

    for record in records:
        base_path = os.environ['HOME']+'/../home/visioncell'
        try:
            #remove webm
            os.remove(base_path+record['filepath_webm'])
            #remove mp4
            os.remove(base_path+record['filepath_mp4'])
            num_removed += 1
            tm_records_db.delete_one({'id': record['id']})
        except Exception as error:
            failed.append(path)

    logs = {
        'num_records': num_to_remove,
        'removed': num_removed,
        'failed': failed
    }
    return logs

