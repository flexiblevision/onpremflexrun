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

settings_path = os.environ['HOME']+'/flex-run'
sys.path.append(settings_path)
import settings

client            = MongoClient("172.17.0.1")
job_collection    = client["fvonprem"]["jobs"]
masks_collection  = client["fvonprem"]["masks"]

CLOUD_DOMAIN = settings.config['cloud_domain'] if 'cloud_domain' in settings.config else "https://clouddeploy.api.flexiblevision.com"

def retrieve_masks(resp_data, token):
    project_ids = resp_data['models'].keys()
    for project_id in project_ids:
        headers = {"Authorization": "Bearer "+token, 'Content-Type': 'application/json'}
        url     = CLOUD_DOMAIN+"/api/capture/mask/get_masks/"+project_id
        res     = requests.get(url, headers=headers, timeout=30)
        data    = res.json()

        if data:
            for mask in data:
                if isinstance(mask,dict):
                    if 'maskId' not in mask: mask['maskId'] = str(uuid.uuid4())
                    
                    query = {'maskName': mask['maskName']}
                    masks_collection.update_one(query, {'$set': mask}, True)

