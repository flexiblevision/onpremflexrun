from pymongo import MongoClient
import datetime
import string
import requests
from bson import json_util, ObjectId
import datetime
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

def get_next_analytics_batch():
        sync_obj = find_utility('last_sync')
        if sync_obj:
            time      = sync_obj[0]['timestamp']
            analytics = analytics_coll.find({"inference_start": {"$gt": time}}) 
            result = json.loads(json_util.dumps(analytics))
            return result


def push_analytics_to_cloud(domain, analytics, access_token):
    headers = {"Authorization": "Bearer "+access_token, 'Content-Type': 'application/json'}
    url = domain+"/api/capture/devices/upload_prediction"
    res = requests.post(url, json=analytics, headers=headers)

    return res.status_code == 200
