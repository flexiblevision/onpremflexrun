from pymongo import MongoClient, ASCENDING
import datetime
import string
import requests
from bson import json_util, ObjectId
import time
import json

client            = MongoClient("172.17.0.1")
job_collection    = client["fvonprem"]["jobs"]
analytics_coll    = client["fvonprem"]["img_analytics"]
util_collection   = client["fvonprem"]["utils"]

def insert_job(job_id, msg):
    job_collection.insert({
        '_id': job_id,
        'type': msg,
        'start_time': str(datetime.datetime.now()),
        'status': 'running'
    })

def find_utility(util_type):
    res = util_collection.find({'type': util_type}, {'_id': 0})
    return json.loads(json_util.dumps(res))

def time_now_ms():
    return int(round(time.time() * 1000))

def get_next_analytics_batch():
    sync_obj = find_utility('predict_sync')
    if sync_obj:
        time      = sync_obj[0]['ms_time']
        analytics = analytics_coll.find({"prediction_start_time": {"$gt": int(time) }})
        result    = json.loads(json_util.dumps(analytics))
        return result
    else:
        util_collection.insert({
            "type": "predict_sync",
            "ms_time": str(time_now_ms())
        })

def cloud_call(url, analytics, headers):
    if not analytics:
        # update_last_sync_on_success(time_now_ms())
        return True

    try:
        res = requests.post(url, json=analytics, headers=headers)
        print(res)
        print('--------------------------------------')
        last_record_timestamp = analytics[-1]['prediction_start_time']
        update_last_sync_on_success(last_record_timestamp)
        time.sleep(2)
        return (res.status_code == 200)
    except:
        print('FAILED TO CALL ', url)
        return False

def update_last_sync_on_success(last_record_timestamp):
    util_collection.update_one(
            {"type": "predict_sync"},
            {"$set": {"ms_time": last_record_timestamp}}, True)

def push_analytics_to_cloud(domain, access_token):
    analytics     = get_next_analytics_batch()
    num_analytics = len(analytics)
    entries_limit = 10

    print('#Analytics: ', num_analytics)
    headers = {"Authorization": "Bearer "+access_token, 'Content-Type': 'application/json'}
    url     = domain+"/api/capture/devices/upload_prediction"

    while num_analytics > entries_limit:
        analytics = analytics[:entries_limit] #take <entries_limit> from the analytics array
        did_sync  = cloud_call(url, analytics, headers)
        if not did_sync:
            print('BREAKING FROM ANALYTICS LOOP')
            break

        analytics     = get_next_analytics_batch()
        num_analytics = len(analytics)
    else:
        analytics = get_next_analytics_batch()
        cloud_call(url, analytics, headers)


    return True