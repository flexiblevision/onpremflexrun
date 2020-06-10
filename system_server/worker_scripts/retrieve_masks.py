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
with open('../../cloud_domain.txt', 'r') as file:
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


# project_ids = ['fb21e45b-cf87-4eb2-8dd9-301900c2f0a9', '0d6868dd-f1fe-43c8-b3b5-f5d3de96ddb3', '9f4bd303-1cee-4769-9af8-4a1e4beba5e7', '255861b5-397e-4cc9-9935-a463419fbf97', '77e8c3f6-83bb-4f07-ad95-66bc95bcbef4', 'bfd4f4ef-1423-45dd-a5d8-b7b4e4b1c3b1', '7fec241c-5ef7-47f4-9248-e5a64b95102d', '9f456eb3-a8fb-4780-be3d-30c328b6b594', '61876ba2-a246-4ced-9c3c-ec5082be4606', 'f736f87d-74ed-4d7a-9a9d-52b0e09819b6', 'f98a0c93-a6a7-458e-9561-0d5fbe2a6b93', '8b1a40ac-b184-40c7-834a-b1748f7b02e8', 'e791b918-5211-4b35-86d9-509e048bdf22', 'd449dd9f-9a56-4ef8-8786-70204b133c8a', '565814dc-974b-4967-860d-185364a246af', 'b4c81aee-7740-4458-905f-5136271e345f', '7675cade-002f-431f-b2e6-069644c83b91', 'f6f704b4-3efb-4dec-bf93-54a2849455bf', '0d6b04fd-e40a-4dfe-8693-11168d0af108', 'ef312b55-0a5d-4f25-a69f-3b37f5b37682', 'a711a9f8-d02d-46f4-a43f-23680726069a', '85db1c95-f24e-4827-930f-7ca833bcc70d', 'e0ef6aff-f35d-4fff-bc90-1dec99c9b6b5']
# token = 'eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6IlJqTTRRa1kyTURnd1JrRTBNREZCT1VKR01UVkRNa0ZFTXpNMk9EVXdPRGxGUkRWRFJqZEVNZyJ9.eyJpc3MiOiJodHRwczovL2F1dGguZmxleGlibGV2aXNpb24uY29tLyIsInN1YiI6ImF1dGgwfDVkOWE2ZDg5Y2MxMjY1MGUyYzdlNGFmYiIsImF1ZCI6WyJodHRwczovL2ZsZXhpYmxldmlzaW9uL2FwaSIsImh0dHBzOi8vZmxleGlibGV2aXNpb24uYXV0aDAuY29tL3VzZXJpbmZvIl0sImlhdCI6MTU4OTk5MjUxMywiZXhwIjoxNTkwMzUyNTEzLCJhenAiOiI1MTJyWUc2WEwzMmszdWlGZzM4SFE4Znl1Yk9PVVVLZiIsInNjb3BlIjoib3BlbmlkIG9mZmxpbmVfYWNjZXNzIiwicGVybWlzc2lvbnMiOltdfQ.Qlk-VHEg8aU46uVpZKSer0UtXtD4UdT_rUpvxaBfEUANKbunmkbP1d7sG7fDnZVIMzdO1uQAsFIX_hNprGTnTmvfLQdBioE2kyjGu6SS2wrmNyfOPKuq9hUxJNkGb2OQGhAQJTDveXo_tMQ-ZpNqJOMf_CkymtGJSlMumB7CD5KzHZIDLr8qjKkACP6-bTkayMGWj8_rwodvDHm7S9eX6qfQ6xOy-AP84it-lvsAHqN76fwVu4EEuOJKozGFiKzTIEl0n2a5U-u9MZzJp-q6g-5gH7f8tr3s9QZt1aJA0JnhS8bFI2X1UKQgbiWtmOFWoRAEUP0wvDQzVl6aaWGscg'


# retrieve_masks(project_ids, token)
