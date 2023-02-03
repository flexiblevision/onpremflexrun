import requests
from pymongo import MongoClient
from bson import json_util, ObjectId
import datetime
import json
import os
import time
import pymongo
from datetime import datetime

client            = MongoClient("172.17.0.1")
tm_records_db     = client["fvonprem"]["event_records"]


CLOUD_FUNCTIONS_BASE = 'https://us-central1-flexible-vision-staging.cloudfunctions.net/'
gcp_functions_path   = os.path.expanduser('~/flex-run/setup_constants/gcp_functions_domain.txt')
with open(gcp_functions_path, 'r') as file:
    CLOUD_FUNCTIONS_BASE = file.read().replace('\n', '')

def mark_as_processed(batch):
    for pf in batch:
        event_id  = pf[0]
        file_path = pf[1][1].name
        tm_records_db.update_one({'id': event_id}, {'$set': {'processed': True, 'processed_time': datetime.now().timestamp()}})
        try:
            os.remove(file_path)
        except Exception as error: 
            print(error, ' ERROR REMOVE ZIP FILE')

def mark_as_dequeued(batch):
    for pf in batch:
        event_id  = pf[0]
        tm_records_db.update_one({'id': event_id}, {'$set': {'queued': False}})

def batch_and_process(events):
    batch_limit = 5
    file_list   = []
    batch       = []
    for event in events:
        if len(batch) == batch_limit:
            file_list.append(batch)
            batch = []
        event_file = (event['id'], (event['zip_name'], open('/home/visioncell'+event['zip_path'], 'rb'), 'application/zip'))
        batch.append(event_file)
    file_list.append(batch) #push remaining files
    return file_list

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
    for batch in batches:
        try:
            push_path = '{}/TMEventIngest'.format(CLOUD_FUNCTIONS_BASE)
            headers   = {'Authorization': 'Bearer '+id_token}
            r = requests.post(push_path, headers=headers, files=batch, timeout=30)
            if r.status_code <= 299:
                mark_as_processed(batch)
            else:
                mark_as_dequeued(batch)
        except Exception as error:
            mark_as_dequeued(batch)        
            print(error, ' ERROR PUSHING ZIP FILE')

    return True
