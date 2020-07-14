import time
import requests
import datetime
from pymongo import MongoClient
import json
from bson import json_util, ObjectId
from jose import jwt
import base64
import re
import subprocess
import os


HOST = 'http://172.17.0.1'
PORT = '5000'
HEADERS = {'referer': HOST}

s = requests.Session()
s.headers.update({'referer': HOST})

client   = MongoClient("172.17.0.1")
util_ref = client["fvonprem"]["utils"]

def get_refresh_token():
    cmd = subprocess.Popen(['cat', os.environ['HOME']+'/flex-run/system_server/creds.txt'], stdout=subprocess.PIPE)
    cmd_out, cmd_err = cmd.communicate()
    cleanStr = cmd_out.strip().decode("utf-8")
    if cleanStr: return cleanStr

def refresh_tokens():
    refresh_token = get_refresh_token()
    if not refresh_token: return False
    path    = '/api/capture/auth/refresh_token'
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json'
    }
    data   = {'refresh_token': refresh_token}
    url = HOST+':'+PORT+path
    try:
        res = s.post(url, headers=headers, json=data)
        tokens = res.json()
        if 'id_token' in tokens and 'access_token' in tokens:
            return {'id_token':tokens['id_token'], 'access_token': tokens['access_token']}
    except:
        print('Failed to refresh token')

    return False

def decode_base64(data, altchars='+/'):
    missing_padding = len(data) % 4
    if missing_padding:
        data += '='* (4 - missing_padding)
    return base64.b64decode(data, altchars).decode('utf-8')

def token_is_valid(token):
    payload = token.split('.')[1]
    data = json.loads(decode_base64(payload))
    time_now = datetime.datetime.now().timestamp()
    token_expiration = data["exp"]
    if token_expiration < time_now:
        print('TOKEN EXPIRED -------------------')
        return False
    else:
        print('TOKEN VALID ---------------')
        return True

def get_auth_token():
    id_token_obj     = util_ref.find_one({'type': 'id_token'}, {'_id': 0})
    access_token_obj = util_ref.find_one({'type': 'access_token'}, {'_id': 0})
    access_token = access_token_obj['token']
    id_token = id_token_obj['token']
    if token_is_valid(id_token) and token_is_valid(access_token):
        return {'access_token': access_token, 'id_token': id_token}
    else:
        print('REFRESHING TOKENS ')
        tokens = refresh_tokens()
        return tokens

    return None

def can_sync():
    path = '/api/capture/system/can_sync'
    url  = HOST+':'+PORT+path
    try:
        res  = s.get(url)
    except:
        print('Failed to request sync')
        time.sleep(5)
        return False

    return res.json()

def check_and_cleanup():
    path   = '/api/capture/system/will_purge_analytics'
    url    = HOST+':'+PORT+path
    tokens = get_auth_token()
    if tokens:
        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + tokens['id_token']
        }
        try:
            res = s.post(url, headers=headers)
            print('CLEANUP RESPONSE: ',res)
        except:
            print('Failed to cleanup')

def sync_device():
    path   = '/api/capture/system/sync_db'
    url    = HOST+':'+PORT+path
    tokens = get_auth_token()
    if can_sync() and tokens:
        print('SYNCING FROM WORKER ----------------', datetime.datetime.now())
        headers = {'Authorization': 'Bearer '+tokens['id_token'],
                  'Access-Token': tokens['access_token']
                }
        try:
            res = s.get(url, headers=headers)
            time.sleep(5)
            check_and_cleanup()
        except:
            print('Failed to sync')
            time.sleep(5)
        
time.sleep(120)

while True:
    time.sleep(1)
    if can_sync():
        #wait to see if browser window handled syncing
        time.sleep(10)
        sync_device()
