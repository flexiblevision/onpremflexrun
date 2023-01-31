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
        tm_records_db.update_one({'id': event['id']}, {'$set': {'processed': True, 'processed_time': datetime.now()}})
        try:
            os.remove(pf['zip_path']+'.zip')
        except Exception as error: print(error, ' ERROR REMOVE ZIP FILE')

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
    event_records = tm_records_db.find({'processed': False, 'storage_type': 'zip_push'})
    events = []
    for i in event_records:
        del i['_id']
        events.append(i)
    return {'count': len(events), 'events': events}

def push_event_records(cloud_domain, access_token, event_records):
    #push the zip file and the event_record to an endpoint
    batches = batch_and_process(event_records['events'])
    for batch in batches:
        try:
            push_path = '{}/TMEventIngest'.format(CLOUD_FUNCTIONS_BASE)
            headers   = {'Authorization': 'Bearer '+access_token}
            r = requests.post(push_path, files=batch, timeout=5)
            if r.status_code <= 299:
                mark_as_processed(batch)
        except Exception as error:
            print(error, ' ERROR PUSHING ZIP FILE')
