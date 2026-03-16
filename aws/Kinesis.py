import json, uuid, boto3
import botocore.exceptions
import os
import datetime
import threading
import requests
from pymongo import MongoClient, ASCENDING
import settings

CLOUD_FUNCTIONS_BASE = settings.config['gcp_functions_domain'] if 'gcp_functions_domain' in settings.config else 'https://us-central1-flexible-vision-staging.cloudfunctions.net/'
FOREIGN_PULL_PATH    = "https://pull-foreign-auth-prod-399393967839.us-central1.run.app"
client   = MongoClient("172.17.0.1")
util_ref = client["fvonprem"]["utils"]
kinesis_log = client["fvonprem"]["kinesis_auth_log"]

def ms_timestamp():
    return int(datetime.datetime.now().timestamp()*1000)

class Kinesis(object):
    def __init__(self):
        self.stream      = None
        self.expiration  = None
        self.REGION_NAME = 'us-east-1'
        self.ACCESS_KEY  = None
        self.SECRET_KEY  = None
        self.CLIENT      = None
        self.authorized  = False
        self.debug       = settings.config.get('kinesis_debug', False)
        self._last_auth_failure = None
        self._auth_lock = threading.Lock()
        self.AUTH_RETRY_MS = 60 * 1000  # wait 60s before retrying after auth failure

        try:
            self.authorize()
        except Exception as error:
            print(error)

    def authorize(self):
        #pull aws keys from cloud
        access_token = self.get_auth_token()
        auth_token   = 'Bearer {}'.format(access_token)
        headers      = {'Authorization': auth_token}
        url          = FOREIGN_PULL_PATH#'{}pull_foreign_auth'.format(CLOUD_FUNCTIONS_BASE)
        data         = {'resource_name': 'aws_kinesis'}
        resp         = requests.post(url, json=data, headers=headers, timeout=30)

        if resp.status_code == 200:
            data = resp.json()
            self.ACCESS_KEY = data['keys']['access_key']
            self.SECRET_KEY = data['keys']['secret_key']
            self.stream     = data['keys']['arn']
            self.expiration = data['expiration']
            self.authorized = True
            self._last_auth_failure = None
            if self.debug:
                self._log('authorize_success', {'expiration': self.expiration})
            return True
        else:
            self.authorized = False
            self._last_auth_failure = ms_timestamp()
            if self.debug:
                self._log('authorize_failed', {'status': resp.status_code, 'body': resp.text[:500]})
            return False

    def validate_expiry(self):
        print('is expired: ', self.expiration < ms_timestamp())
        if self.expiration < ms_timestamp():
            self.authorize()

    def _connect_client(self):
        """ Connect to Kinesis Streams """
        REFRESH_BUFFER_MS = 5 * 60 * 1000  # refresh 5 minutes before expiry

        needs_refresh = (
            not self.expiration or
            ms_timestamp() > (self.expiration - REFRESH_BUFFER_MS)
        )

        if needs_refresh:
            with self._auth_lock:
                # re-check after acquiring lock — another thread may have already refreshed
                needs_refresh = (
                    not self.expiration or
                    ms_timestamp() > (self.expiration - REFRESH_BUFFER_MS)
                )
                if not needs_refresh:
                    pass  # another thread already refreshed
                elif self._last_auth_failure and ms_timestamp() - self._last_auth_failure < self.AUTH_RETRY_MS:
                    if self.debug:
                        self._log('auth_retry_skipped', {'ms_since_failure': ms_timestamp() - self._last_auth_failure})
                    return False
                else:
                    did_authorize = self.authorize()
                    if did_authorize:
                        self.CLIENT = boto3.client('kinesis',
                                            region_name=self.REGION_NAME,
                                            aws_access_key_id=self.ACCESS_KEY,
                                            aws_secret_access_key=self.SECRET_KEY)
                    else:
                        return False

        if not self.CLIENT and self.ACCESS_KEY:
            self.CLIENT = boto3.client('kinesis',
                                region_name=self.REGION_NAME,
                                aws_access_key_id=self.ACCESS_KEY,
                                aws_secret_access_key=self.SECRET_KEY)

        if self.CLIENT:
            self.authorized = True
            return self.CLIENT

        return False

    def send_stream(self, data, partition_key=None):
        if not self.authorized:
            if self.debug:
                self._log('send_rejected', {'reason': 'not authorized'})
            return 'Service not authorized', 403

        # If no partition key is given, assume random sharding for even shard write load
        if partition_key == None:
            partition_key = uuid.uuid4()

        client = self._connect_client()
        if client:
            try:
                client.put_record(
                    StreamARN=self.stream,
                    Data=json.dumps(data)+'\n',
                    PartitionKey=str(partition_key)
                )
                return True
            except botocore.exceptions.ClientError as e:
                error_code = e.response['Error']['Code']
                if error_code in ('UnrecognizedClientException', 'ExpiredTokenException', 'InvalidSignatureException'):
                    self._log('put_record_auth_error', {'error_code': error_code, 'message': str(e)})
                    self.CLIENT = None
                    self.expiration = None
                raise
        else:
            return False

    def _log(self, event, details=None):
        try:
            kinesis_log.insert_one({
                'event': event,
                'timestamp': datetime.datetime.utcnow(),
                'ts_ms': ms_timestamp(),
                'details': details or {}
            })
        except Exception:
            pass

    def get_auth_token(self):
        access_token_obj = util_ref.find_one({'type': 'access_token'}, {'_id': 0})
        access_token = access_token_obj['token']
        return access_token