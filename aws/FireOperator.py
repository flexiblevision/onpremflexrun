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
import subprocess
import sys
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

_db = None


def get_db():
    """
    The Firestore handle, built on first use rather than at import.

    Constructing it at module scope made this module impossible to import
    without cloud credentials, which is why every test that touches it - and so
    the whole pytest run, since a collection error aborts everything - failed on
    any machine and in CI.

    It also produced the wrong error on a device. credentials=None does not mean
    "no credentials" to the Google SDK; it means "go and find Application
    Default Credentials". So a device with no fire_creds.json died on an opaque
    DefaultCredentialsError instead of being told which file was missing.
    """
    global _db
    if _db is None:
        if cred is None:
            raise RuntimeError(
                'FireOperator needs credentials at {}. Refusing to fall back to '
                'Application Default Credentials, which is what turned a missing '
                'config file into an unreadable auth error.'.format(FIRESTORE_CREDS))
        _db = firestore.Client(project="testingprivateapis",
                               credentials=cred, database=db_name)
    return _db

client   = MongoClient("172.17.0.1")
util_ref = client["fvonprem"]["utils"]

def ms_timestamp():
    return int(datetime.datetime.now().timestamp()*1000)

class FireOperator:
    HEARTBEAT_INTERVAL_S = 60
    HEARTBEAT_TIMEOUT_S  = 150  # 2.5x interval — resubscribe if no snapshot in this window
    WATCHDOG_INTERVAL_S  = 30

    def __init__(self):
        db                 = get_db()
        self.db            = db
        self.collection    = collection
        self.document      = document
        self.capture_doc   = db.collection("inspections").document(self.document)
        self.status_doc    = db.collection("status").document(self.document)
        self.heartbeat_doc = db.collection("heartbeat").document(self.document)
        self.thread        = threading.Event()
        self.trigger_dest  = trigger_dest
        self.last_read_time = None
        self.intialized    = False

        self._capture_watch   = None
        self._heartbeat_watch = None
        self.last_heartbeat_seen = time.time()  # seed so watchdog doesn't fire before first heartbeat

        self.start_listener()
        threading.Thread(target=self._heartbeat_writer, daemon=True).start()
        threading.Thread(target=self._watchdog, daemon=True).start()

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

    def _heartbeat_listener(self, doc_snapshot, changes, read_time):
        # Any snapshot here proves the gRPC stream is alive end-to-end.
        self.last_heartbeat_seen = time.time()

    def start_listener(self):
        for w in (self._capture_watch, self._heartbeat_watch):
            if w is not None:
                try:
                    w.unsubscribe()
                except Exception as e:
                    print(f"FireOperator: unsubscribe error: {e}")
        self.intialized = False  # suppress trigger on the snapshot replay
        self.last_heartbeat_seen = time.time()
        self._capture_watch   = self.capture_doc.on_snapshot(self.listener)
        self._heartbeat_watch = self.heartbeat_doc.on_snapshot(self._heartbeat_listener)
        print(f"FireOperator: listeners (re)subscribed at {datetime.datetime.now()}")

    def _heartbeat_writer(self):
        while True:
            time.sleep(self.HEARTBEAT_INTERVAL_S)
            try:
                self.heartbeat_doc.set({'ts': ms_timestamp()})
            except Exception as e:
                print(f"FireOperator: heartbeat write failed: {e}")

    def _watchdog(self):
        while True:
            time.sleep(self.WATCHDOG_INTERVAL_S)
            try:
                age = time.time() - self.last_heartbeat_seen
                if age > self.HEARTBEAT_TIMEOUT_S:
                    print(f"FireOperator: heartbeat stale ({age:.0f}s), resubscribing")
                    self.start_listener()
            except Exception as e:
                print(f"FireOperator: watchdog error: {e}")

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

def run_operator():
    if 'use_aws' in settings.config and settings.config['use_aws']:
        fo_server_path = os.path.join(os.environ.get('HOME', '.'), 'flex-run', 'aws', 'fo_server.py')
        print(f"Checking if '{fo_server_path}' is already running via 'forever'...")

        try:
            result = subprocess.run(['forever', 'list'], capture_output=True, text=True, check=True)
            forever_list_output = result.stdout

            is_fo_server_running = False
            for line in forever_list_output.splitlines():
                if fo_server_path in line and "STOPPED" not in line:
                    is_fo_server_running = True
                    break # Found a running instance, no need to check further

            if is_fo_server_running:
                print(f"WARNING: '{fo_server_path}' is already running via 'forever'.")
                print("Skipping Flask server initialization to avoid conflicts.")
                return 'skipped'
            else:
                print(f"'{fo_server_path}' is not detected as running via 'forever' (or is stopped).")
                print(f"Starting '{fo_server_path}' using forever...")
                subprocess.Popen(['forever', 'start', '-c', 'python3', fo_server_path],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                print(f"'{fo_server_path}' started via forever.")
                return 'started'

        except subprocess.CalledProcessError as e:
            print(f"ERROR: 'forever list' command failed with exit code {e.returncode}: {e.stderr.strip()}")
            print("Proceeding with Flask server initialization, but 'forever' check failed.")
        except Exception as e:
            print(f"An unexpected error occurred during 'forever' check: {e}")
            print("Proceeding with Flask server initialization, but 'forever' check failed.")
