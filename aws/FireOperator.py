import threading 
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

import time
import requests
import settings
import os
import datetime
from pymongo import MongoClient, ASCENDING

cred = None
FIRESTORE_CREDS = f"{os.environ['HOME']}/fire_creds.json"
if os.path.isfile(FIRESTORE_CREDS):
    cred = service_account.Credentials.from_service_account_file(FIRESTORE_CREDS)

db_name, collection, document, trigger_dest = "", "", "", "http://172.17.0.1:1880/trigger"
if 'fire_operator' in settings.config:
    db_name      = settings.config['fire_operator']['db_name']
    document     = settings.config['fire_operator']['document'] #(warehouse_zone)
    trigger_dest = settings.config['fire_operator']['trigger_dest']

db = firestore.Client(project="flexible-vision-staging", credentials=cred, database=db_name) 

client   = MongoClient("172.17.0.1")
util_ref = client["fvonprem"]["utils"]

def ms_timestamp():
    return int(datetime.datetime.now().timestamp()*1000)

class FireOperator:
    def __init__(self):
        self.db           = db
        self.collection   = collection
        self.document     = document
        self.capture_doc  = db.collection("inspections").document(self.document)
        self.status_doc   = db.collection("status").document(self.document)
        self.thread       = threading.Event()
        self.trigger_dest = trigger_dest
        self.last_read_time = None
        self.intialized   = False

        self.start_listener()

    def syncing_alive(self):
        last_sync_ref = util_ref.find_one({'type': 'predict_sync'}, {'_id': 0})
        sync_enabled_ref = util_ref.find_one({'type': 'sync'}, {'_id': 0})
        sync_interval_ref = util_ref.find_one({'type': 'sync_interval'}, {'_id': 0})

        sync_enabled  = sync_enabled_ref['is_enabled']
        last_sync     = int(last_sync_ref['ms_time'])
        sync_interval = int(sync_interval_ref['interval'])

        if sync_enabled == False and (last_sync + ((60000*sync_interval) * 10)) > ms_timestamp():
            return True
        else:
            # update status to rejected because of waiting for sync service...
            status = {}
            self.update_status(status)
            return False

    def listener(self, doc_snapshot, changes, read_time):
        for doc in doc_snapshot:
            print(f"Received document snapshot: {doc.id}")
            trigger_record = doc.to_dict()
            self.last_read_time = read_time
            if self.intialized: 
                requests.post(self.trigger_dest, json=trigger_record, timeout=10)
            else:
                self.intialized = True

        self.thread.set()

    def start_listener(self):
        self.capture_doc.on_snapshot(self.listener)

    def update_status(self, status):
        self.status_doc.set(status)

    def get_status(self):
        status_ref = self.status_doc
        doc = status_ref.get()
        if doc.exists:
            return doc.to_dict()
        else:
            return None

    def get_status_by_service_account(self):
        url = 'https://us-central1-testingprivateapis.cloudfunctions.net/get-status-by-service-account'
        creds = service_account.IDTokenCredentials.from_service_account_file(
            FIRESTORE_CREDS, target_audience=url)

        authed_session = AuthorizedSession(creds)
        resp = authed_session.post(self.document)
        return resp.json()