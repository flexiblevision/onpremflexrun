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
from cloud_env import get_cloud_domain

client              = MongoClient("172.17.0.1")
job_collection      = client["fvonprem"]["jobs"]
programs_collection = client["fvonprem"]["programs"]

CLOUD_DOMAIN = settings.config['cloud_domain'] if 'cloud_domain' in settings.config else "https://clouddeploy.api.flexiblevision.com"

# The cloud clamps page_size to 99 whatever is asked for.
PAGE_SIZE = 99


def retrieve_programs(resp_data, token):
    project_ids = resp_data['models'].keys()
    headers = {"Authorization": "Bearer "+token, 'Content-Type': 'application/json'}
    for project_id in project_ids:
        for program in cloud_programs(project_id, headers):
            program['model'] = format_filename(program['model'])
            query = {'id': program['id']}
            programs_collection.update_one(query, {'$set': program}, True)


def cloud_programs(project_id, headers):
    records, start = [], 0
    while True:
        url = "%s/api/capture/programs/%s/%d/%d?use_latest=true" % (
            get_cloud_domain(CLOUD_DOMAIN), project_id, start, PAGE_SIZE)
        res = requests.get(url, headers=headers, timeout=5)
        res.raise_for_status()
        data = res.json() or {}
        page = data.get('records') or []
        records.extend(page)
        start += len(page)
        count = data.get('count')
        done = start >= count if count is not None else len(page) < PAGE_SIZE
        if not page or done:
            return records


def format_filename(s):
    valid_chars = "-_.() %s%s" % (string.ascii_letters, string.digits)
    filename = ''.join(c for c in s if c in valid_chars)
    filename = filename.replace(' ','_')
    return filename