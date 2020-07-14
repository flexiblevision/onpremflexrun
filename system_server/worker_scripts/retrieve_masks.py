import requests
import os
import sys
import zipfile, io
import base64
import io
import time
import uuid
from collections import defaultdict
from io import StringIO
from io import BytesIO
from pymongo import MongoClient
import datetime
import string

client            = MongoClient("172.17.0.1")
job_collection    = client["fvonprem"]["jobs"]
masks_collection  = client["fvonprem"]["masks"]

CLOUD_DOMAIN = "https://clouddeploy.api.flexiblevision.com"
cloud_path   = os.path.expanduser('~/flex-run/setup_constants/cloud_domain.txt')
with open(cloud_path, 'r') as file:
    CLOUD_DOMAIN = file.read().replace('\n', '')

def retrieve_masks(resp_data, token):
    job_id = str(uuid.uuid4())
    insert_job_ref(job_id)
    project_ids = resp_data['models'].keys()
    for project_id in project_ids:
        headers = {"Authorization": "Bearer "+token, 'Content-Type': 'application/json'}
        url     = CLOUD_DOMAIN+"/api/capture/mask/get_masks/"+project_id
        res     = requests.get(url, headers=headers)
        data    = res.json()

        if data:
            for mask in data:
                if isinstance(mask,dict):
                    if 'maskId' not in mask: mask['maskId'] = str(uuid.uuid4())
                    
                    query = {'maskName': mask['maskName']}
                    masks_collection.update_one(query, {'$set': mask}, True)
    delete_job_ref(job_id)

def insert_job_ref(job_id):
    job_collection.insert_one({
        '_id': job_id,
        'type': 'syncing_masks',
        'start_time': str(datetime.datetime.now()),
        'status': 'running'
        })

def delete_job_ref(job_id):
    query = {'_id': job_id}
    job_collection.delete_one(query)


