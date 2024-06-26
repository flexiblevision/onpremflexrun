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
import settings

client              = MongoClient("172.17.0.1")
job_collection      = client["fvonprem"]["jobs"]
programs_collection = client["fvonprem"]["programs"]

CLOUD_DOMAIN = settings.config['cloud_domain'] if 'cloud_domain' in settings.config else "https://clouddeploy.api.flexiblevision.com"

def retrieve_programs(resp_data, token):
    project_ids = resp_data['models'].keys()
    for project_id in project_ids:
        headers = {"Authorization": "Bearer "+token, 'Content-Type': 'application/json'}
        url     = CLOUD_DOMAIN+"/api/capture/programs/"+project_id+"/0/9999?use_latest=true"
        res     = requests.get(url, headers=headers, timeout=5)
        data    = res.json()

        if data:
            for program in data['records']:
                program['model'] = format_filename(program['model'])
                query = {'id': program['id']}
                programs_collection.update_one(query, {'$set': program}, True)


def format_filename(s):
    valid_chars = "-_.() %s%s" % (string.ascii_letters, string.digits)
    filename = ''.join(c for c in s if c in valid_chars)
    filename = filename.replace(' ','_')
    return filename