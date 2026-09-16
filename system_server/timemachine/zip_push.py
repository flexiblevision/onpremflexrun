import requests
from pymongo import MongoClient
from bson import json_util, ObjectId
import datetime
import json
import os
import time
import pymongo
from datetime import datetime
import settings
from cloud_env import get_cloud_functions_base
from timemachine import analytics

client            = MongoClient("172.17.0.1")
tm_records_db     = client["fvonprem"]["event_records"]
utils_db          = client["fvonprem"]["utils"]
dev_ref           = utils_db.find_one({'type':'device_id'})
DEV_ID            =  None if not dev_ref else dev_ref['id']

CLOUD_FUNCTIONS_BASE = settings.config['gcp_functions_domain'] if 'gcp_functions_domain' in settings.config else 'https://functions-proxy.flexiblevision.com/'

def mark_as_processed(files, events):
    for pf, event in zip(files, events):
        file_path = pf[1][1].name
        tm_records_db.update_one({'id': event['id']}, {'$set': {'processed': True, 'processed_time': datetime.now().timestamp()}})
        # Only now is the recording known to be in the cloud.
        analytics.record_event(event, device_id=DEV_ID)
        try:
            os.remove(file_path)
        except Exception as error:
            print(error, ' ERROR REMOVE ZIP FILE')

def mark_as_dequeued(events):
    for event in events:
        tm_records_db.update_one({'id': event['id']}, {'$set': {'queued': False}})

def batch_and_process(events):
    """[(files, events)] — the multipart POST, and the records to mark after it.

    The field name is the *device* id, shared across a batch, so the upload
    tuple alone cannot say which record to mark.
    """
    batch_limit = 5
    batches     = []
    files       = []
    batch_events = []
    for event in events:
        if len(files) == batch_limit:
            batches.append((files, batch_events))
            files, batch_events = [], []

        dev_id = DEV_ID if DEV_ID else event['id']
        files.append((dev_id, (event['zip_name'], open('/home/visioncell'+event['zip_path'], 'rb'), 'application/zip')))
        batch_events.append(event)
    batches.append((files, batch_events)) #push remaining files
    return batches

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

def push_event_records(cloud_domain, id_token, event_records):
    #push the zip file and the event_record to an endpoint
    batches = batch_and_process(event_records['events'])
    for files, events in batches:
        if not files:
            continue
        try:
            push_path = '{}TMEventIngest'.format(get_cloud_functions_base(CLOUD_FUNCTIONS_BASE))
            headers   = {'Authorization': 'Bearer '+id_token}
            r = requests.post(push_path, headers=headers, files=files, timeout=30)
            if r.status_code <= 299:
                mark_as_processed(files, events)
            else:
                mark_as_dequeued(events)
        except Exception as error:
            mark_as_dequeued(events)
            print(error, ' ERROR PUSHING ZIP FILE')

    return True
