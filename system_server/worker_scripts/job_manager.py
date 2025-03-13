from pymongo import MongoClient, ASCENDING
import datetime
import string
import requests
from bson import json_util, ObjectId
import time
import json
import sys
import os 

settings_path = os.environ['HOME']+'/flex-run'
sys.path.append(settings_path)
import settings

from redis import Redis
from rq import Queue, Retry, Worker, Connection
from rq.job import Job
redis_con   = Redis('localhost', 6379, password=None)
job_queue   = Queue('default', connection=redis_con)

client            = MongoClient("172.17.0.1")
job_collection    = client["fvonprem"]["jobs"]
analytics_coll    = client["fvonprem"]["img_analytics"]
util_collection   = client["fvonprem"]["utils"]
use_aws           = False 
aws_client        = None
config            = settings.config
BATCH_SIZE        = 5
BQ_INGEST_PATH    = "https://data-ingest-queue-172198548516.us-central1.run.app"


if 'use_aws' in config and config['use_aws']:
    use_aws    = True
    aws_client = settings.kinesis

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
        analytics = analytics_coll.find({"modified": {"$gt": int(time), "$lt": int(time+5000)}}).sort("modified", 1)
        result    = json.loads(json_util.dumps(analytics))
        return result
    else:
        util_collection.insert({
            "type": "predict_sync",
            "ms_time": str(time_now_ms())
        })

def get_unsynced_records():
    sync_obj = find_utility('predict_sync')
    if sync_obj:
        time      = sync_obj[0]['ms_time']

        sync_time = time_now_ms() - 30000
        query     = {"synced": False, "modified": {"$lt": int(sync_time)}}
        analytics = analytics_coll.find(query).limit(BATCH_SIZE)
        result    = json.loads(json_util.dumps(analytics))

        for i in result:
            mark_as_processing(i['id'])

        return result
    else:
        util_collection.insert({
            "type": "predict_sync",
            "ms_time": str(time_now_ms())
        })

def mark_as_processing(record_id):
    analytics_coll.update_one({"id": record_id},
        {"$set": {"synced": "processing"}}, True)

def mark_as_synced(record_id):
    analytics_coll.remove({"id": record_id})
    # analytics_coll.update_one(
    #     {"id": record_id},
    #     {"$set": {"synced": True}}, True)

def cloud_call(url, analytics, headers):
    if not analytics:
        # update_last_sync_on_success(time_now_ms())
        return True
    try:
        # last_record_timestamp = analytics[-1]['modified']
        # update_last_sync_on_success(last_record_timestamp)
        res = requests.post(url, json=analytics, headers=headers, timeout=20)
        bq_res = requests.post(BQ_INGEST_PATH, json=analytics, headers=headers, timeout=20)
        print(res, bq_res)
        print('--------------------------------------')
        success = res.status_code == 200 and bq_res.status_code == 200
        if success:
            for i in analytics: mark_as_synced(i['id'])

        return success
    except:
        print('FAILED TO CALL ', url)
        return False

def kinesis_call(analytics):
    if not analytics:
        return True
    try:
        for a in analytics:
            if '_id' in a: del a['_id']
            did_send = aws_client.send_stream(a)
            mark_as_synced(a['id'])
            print(did_send)
        print('--------------------------------------')
        return did_send
    except Exception as error:
        print('FAILED TO POST TO KINESIS')
        return False

def update_last_sync_on_success(last_record_timestamp):
    util_collection.update_one(
            {"type": "predict_sync"},
            {"$set": {"ms_time": last_record_timestamp}}, True)

def push_analytics_to_cloud_batch(domain, access_token):
    #analytics     = get_next_analytics_batch()
    analytics     = get_unsynced_records()
    num_analytics = len(analytics)
    entries_limit = BATCH_SIZE

    if num_analytics == 0:
        return

    print('#Analytics: ', num_analytics)
    headers = {"Authorization": "Bearer "+access_token, 'Content-Type': 'application/json'}
    url     = domain+"/api/capture/devices/upload_prediction"

    update_last_sync_on_success(analytics[-1]['modified'])
    start, end, count = 0, entries_limit, 0
    while count < num_analytics:

        a = analytics[start:end] #take <entries_limit> from the analytics array
        if use_aws:
            j_push = job_queue.enqueue(kinesis_call, a, job_timeout=99999999, result_ttl=-1)
            if j_push: insert_job(j_push.id, 'Syncing_'+str(len(a))+'_with_cloud')
        else:
            j_push = job_queue.enqueue(cloud_call, url, a, headers, job_timeout=99999999, result_ttl=-1)
            if j_push: insert_job(j_push.id, 'Syncing_'+str(len(a))+'_with_cloud')
        start += entries_limit
        end += entries_limit
        count += len(a)

    return True

def push_analytics_to_cloud(domain, access_token):
    headers = {"Authorization": "Bearer "+access_token, 'Content-Type': 'application/json'}
    url     = domain+"/api/capture/devices/upload_prediction"

    num_analytics = 1
    while num_analytics > 0:
        analytics = get_unsynced_records()
        num_analytics = len(analytics)
        if num_analytics <= 0: return True
        print('#Analytics: ', num_analytics)
        if use_aws:
            j_push = job_queue.enqueue(kinesis_call, analytics, job_timeout=99999999, result_ttl=-1)
            if j_push: insert_job(j_push.id, 'Syncing_'+str(len(analytics))+'_with_cloud')
        else:
            j_push = job_queue.enqueue(cloud_call, url, analytics, headers, job_timeout=99999999, result_ttl=-1)
            if j_push: insert_job(j_push.id, 'Syncing_'+str(len(analytics))+'_with_cloud')

    return True

def enable_ocr():
    install_file = f"{os.environ['HOME']}/flex-run/helpers/install_ocr.sh"
    os.system(f"sudo sh {install_file}")