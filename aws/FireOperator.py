import threading 
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
from google.oauth2 import service_account
import time
import requests
import settings
import os

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

        self.start_listener()

    def listener(self, doc_snapshot, changes, read_time):
        for doc in doc_snapshot:
            print(f"Received document snapshot: {doc.id}")
            trigger_record = doc.to_dict()
            self.last_read_time = read_time
            requests.post(self.trigger_dest, json=trigger_record)

        self.thread.set()

    def start_listener(self):
        self.capture_doc.on_snapshot(self.listener)

    def update_status(self, status):
        self.status_doc.set(status)