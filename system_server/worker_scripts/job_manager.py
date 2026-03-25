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
from rq import Queue, Retry, Worker
from rq.job import Job
redis_con   = Redis('localhost', 6379, password=None)
job_queue   = Queue('default', connection=redis_con)
sync_tracker = False

# Sync tracking configuration
SYNC_COMPLETION_THRESHOLD = 74
TRACKER_COLLECTION_NAME = "sync_tracker"  # Separate collection for tracking data


MONGODB_HOST = "172.17.0.1"  # Should move to config/environment variable
MONGODB_PORT = 27017
MONGODB_TIMEOUT_MS = 5000  # 5 second timeout
MONGODB_MAX_POOL_SIZE = 50
MONGODB_SERVER_SELECTION_TIMEOUT_MS = 5000

try:
    client = MongoClient(
        host=MONGODB_HOST,
        port=MONGODB_PORT,
        serverSelectionTimeoutMS=MONGODB_SERVER_SELECTION_TIMEOUT_MS,
        connectTimeoutMS=MONGODB_TIMEOUT_MS,
        socketTimeoutMS=MONGODB_TIMEOUT_MS,
        maxPoolSize=MONGODB_MAX_POOL_SIZE,
        retryWrites=True,
        retryReads=True
    )
    client.admin.command('ping')
    print(f"Successfully connected to MongoDB at {MONGODB_HOST}")
except Exception as e:
    print(f"FATAL: Failed to connect to MongoDB: {str(e)}")
    sys.exit(1)

job_collection    = client["fvonprem"]["jobs"]
analytics_coll    = client["fvonprem"]["img_analytics"]
util_collection   = client["fvonprem"]["utils"]
use_aws           = False 
aws_client        = None
config            = settings.config
BATCH_SIZE        = 10
LB_DOMAIN         = "https://functions-proxy.flexiblevision.com"
BQ_INGEST_PATH    = "https://data-ingest-queue-172198548516.us-central1.run.app"
if config['latest_stable_ref'] == 'latest_stable_version':
    #use prod endpoint
    BQ_INGEST_DIRECT = "https://data-enqueue-prod-172198548516.us-central1.run.app"
    try:
        r = requests.get(LB_DOMAIN, timeout=5)
        BQ_INGEST_PATH = LB_DOMAIN + "/data-enqueue-prod"
        print(f"LB reachable, using proxy: {BQ_INGEST_PATH}")
    except Exception:
        BQ_INGEST_PATH = BQ_INGEST_DIRECT
        print(f"LB unreachable, using direct: {BQ_INGEST_PATH}")

if 'use_aws' in config and config['use_aws']:
    use_aws    = True
    aws_client = settings.kinesis


def _get_tracker_collection():
    """Get the dedicated tracker collection"""
    return client["fvonprem"][TRACKER_COLLECTION_NAME]

def update_sync_tracker(did, success=True, error_msg=None, record_id=None):
    """Update sync tracker for a detection - uses atomic MongoDB operations"""
    current_time_ms = time_now_ms()
    current_time_iso = datetime.datetime.now().isoformat()

    try:
        tracker_coll = _get_tracker_collection()

        if success:
            result = tracker_coll.find_one_and_update(
                {'_id': did, 'completed': {'$ne': True}},
                {
                    '$inc': {'count': 1},
                    '$set': {
                        'last_sync': current_time_iso,
                        'last_sync_ms': current_time_ms
                    },
                    '$setOnInsert': {
                        'errors': [],
                        'completed': False,
                        'first_sync': current_time_iso,
                        'first_sync_ms': current_time_ms,
                        'completion_time': None,
                        'completion_time_ms': None,
                        'total_time_seconds': None
                    }
                },
                upsert=True,
                return_document=True
            )

            if result and result['count'] >= SYNC_COMPLETION_THRESHOLD and not result.get('completed'):
                elapsed_seconds = (current_time_ms - result['first_sync_ms']) / 1000.0
                tracker_coll.update_one(
                    {'_id': did, 'completed': {'$ne': True}},
                    {'$set': {
                        'completed': True,
                        'completion_time': current_time_iso,
                        'completion_time_ms': current_time_ms,
                        'total_time_seconds': elapsed_seconds
                    }}
                )
        else:
            error_entry = {
                'timestamp': current_time_iso,
                'timestamp_ms': current_time_ms,
                'record_id': record_id,
                'error': error_msg
            }
            tracker_coll.update_one(
                {'_id': did},
                {
                    '$push': {'errors': error_entry},
                    '$set': {
                        'last_sync': current_time_iso,
                        'last_sync_ms': current_time_ms
                    },
                    '$setOnInsert': {
                        'count': 0,
                        'completed': False,
                        'first_sync': current_time_iso,
                        'first_sync_ms': current_time_ms,
                        'completion_time': None,
                        'completion_time_ms': None,
                        'total_time_seconds': None
                    }
                },
                upsert=True
            )

    except Exception as e:
        print(f"Error updating sync_tracker for {did}: {str(e)}")


def insert_job(job_id, msg):
    job_collection.insert_one({
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

def get_unsynced_records():
    sync_obj = find_utility('predict_sync')
    if not sync_obj:
        util_collection.insert_one({
            "type": "predict_sync",
            "ms_time": str(time_now_ms())
        })
        return []
    
    sync_time = time_now_ms() - 120000
    query = {
        "synced": False,
        "modified": {"$lt": int(sync_time)}
    }
    
    if use_aws:
        query['complete'] = True
    
    result = []
    batch_size = 1000  # Reasonable limit
    
    for _ in range(batch_size):
        record = analytics_coll.find_one_and_update(
            query,
            {"$set": {"synced": "processing", "modified": time_now_ms()}},
            return_document=True  # Return the updated document
        )
        
        if not record:
            break  # No more records matching query
            
        result.append(json.loads(json_util.dumps(record)))
    
    one_hour_ago_ms = time_now_ms() - (60000*60)
    five_hours_ago_ms = time_now_ms() - ((60000*60)*5)
    
    stuck_query = {
        "synced": "processing",
        "modified": {
            "$lt": one_hour_ago_ms,
            "$gt": five_hours_ago_ms
        }
    }
    
    for _ in range(20):
        stuck_record = analytics_coll.find_one_and_update(
            stuck_query,
            {"$set": {"synced": "processing", "modified": time_now_ms()}},
            return_document=True
        )
        
        if not stuck_record:
            break
            
        result.append(json.loads(json_util.dumps(stuck_record)))
    
    time.sleep(1)
    return result

def mark_as_processing(record_id):
    analytics_coll.update_one({"id": record_id},
        {"$set": {"synced": "processing", "modified": time_now_ms()}}, True)

def mark_as_synced(record_id):
    analytics_coll.delete_one({"id": record_id})

def cloud_call(url, analytics, headers):
    if not analytics:
        return True
    try:
        for a in analytics: a['synced'] = True
        res = requests.post(url, json=analytics, headers=headers, timeout=20)
        bq_res = requests.post(BQ_INGEST_PATH, json=analytics, headers=headers, timeout=20)
        print(res, bq_res)
        print('--------------------------------------')
        success = res.status_code == 200
        if success:
            for i in analytics:
                mark_as_synced(i['id'])
                if sync_tracker:
                    did = i.get('did', 'unknown')
                    update_sync_tracker(did, success=True, record_id=i['id'])
        else:
            # Track failed syncs and mark for retry
            for i in analytics:
                did = i.get('did', 'unknown')
                analytics_coll.update_one({"id": i['id']}, {"$set": {"synced": False}})
                if sync_tracker:
                    update_sync_tracker(
                        did,
                        success=False,
                        error_msg=f"HTTP {res.status_code}: {res.text[:200]}",
                        record_id=i['id']
                    )
        time.sleep(1)
        return success
    except Exception as e:
        error_msg = f"Exception in cloud_call: {str(e)}"
        print(f'FAILED TO CALL {url}: {error_msg}')
        # Track failed syncs for all records in batch and mark for retry
        for i in analytics:
            did = i.get('did', 'unknown')
            analytics_coll.update_one({"id": i['id']}, {"$set": {"synced": False}})
            if sync_tracker:
                update_sync_tracker(
                    did,
                    success=False,
                    error_msg=error_msg,
                    record_id=i['id']
                )
        return False

def kinesis_call(analytics):
    if not analytics:
        return True
    
    overall_success = True
    try:
        for a in analytics:
            record_id = a.get('id', 'unknown')
            did = a.get('did', 'unknown')
            
            # Check if record exists
            if 'id' not in a:
                error_msg = "Record missing 'id' field"
                if sync_tracker:
                    update_sync_tracker(did, success=False, error_msg=error_msg, record_id=record_id)
                overall_success = False
                continue
            
            if 'did' not in a:
                error_msg = "Record missing 'did' field"
            
            if '_id' in a: 
                del a['_id']
            
            try:
                did_send = aws_client.send_stream(a)

                if did_send is True:
                    mark_as_synced(record_id)
                    if sync_tracker:
                        update_sync_tracker(did, success=True, record_id=record_id)
                    print(f"Kinesis send success: {did_send}")
                else:
                    error_msg = "Kinesis send returned False"
                    analytics_coll.update_one({"id": record_id}, {"$set": {"synced": False}})
                    if sync_tracker:
                        update_sync_tracker(did, success=False, error_msg=error_msg, record_id=record_id)
                    overall_success = False

            except Exception as inner_error:
                error_msg = f"Kinesis send exception: {str(inner_error)}"
                analytics_coll.update_one({"id": record_id}, {"$set": {"synced": False}})
                if sync_tracker:
                    update_sync_tracker(did, success=False, error_msg=error_msg, record_id=record_id)
                overall_success = False

        time.sleep(1)
        print('--------------------------------------')
        return overall_success
        
    except Exception as error:
        error_msg = f"FAILED TO POST TO KINESIS: {str(error)}"
        print(error_msg)

        # Track all records as failed and mark for retry
        for a in analytics:
            did = a.get('did', 'unknown')
            record_id = a.get('id', 'unknown')
            analytics_coll.update_one({"id": record_id}, {"$set": {"synced": False}})
            if sync_tracker:
                update_sync_tracker(did, success=False, error_msg=error_msg, record_id=record_id)

        return False

def push_analytics_to_cloud(domain, access_token):
    headers = {"Authorization": "Bearer "+access_token, 'Content-Type': 'application/json'}
    url     = domain+"/api/capture/devices/upload_prediction"

    latest_analytics = get_unsynced_records()
    num_analytics = len(latest_analytics)

    if num_analytics == 0:
        return True

    for i in range(0, num_analytics, BATCH_SIZE):
        analytics = latest_analytics[i:i+BATCH_SIZE]
        if not analytics: break

        print('#Analytics: ', len(analytics))
        if use_aws:
            j_push = job_queue.enqueue(
                kinesis_call,
                analytics,
                job_timeout=300,
                result_ttl=3600,
                retry=Retry(max=5, interval=60)
            )
            if j_push: insert_job(j_push.id, 'Syncing_'+str(len(analytics))+'_with_cloud')
        else:
            j_push = job_queue.enqueue(
                cloud_call,
                url,
                analytics,
                headers,
                job_timeout=300,
                result_ttl=3600,
                retry=Retry(max=5, interval=60)
            )
            if j_push: insert_job(j_push.id, 'Syncing_'+str(len(analytics))+'_with_cloud')

    return True


def enable_ocr():
    install_file = f"{os.environ['HOME']}/flex-run/helpers/install_ocr.sh"
    os.system(f"sudo sh {install_file}")