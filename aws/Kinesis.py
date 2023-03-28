import json, uuid, boto3
import os
import datetime
import requests
from pymongo import MongoClient, ASCENDING

CLOUD_FUNCTIONS_BASE = 'https://us-central1-flexible-vision-staging.cloudfunctions.net/'
gcp_functions_path   = os.path.expanduser('~/flex-run/setup_constants/gcp_functions_domain.txt')
with open(gcp_functions_path, 'r') as file:
    CLOUD_FUNCTIONS_BASE = file.read().replace('\n', '')

client   = MongoClient("172.17.0.1")
util_ref = client["fvonprem"]["utils"]

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

        self.authorize()

    def authorize(self):
        #pull aws keys from cloud
        access_token = self.get_auth_token()
        print(access_token)
        auth_token   = 'Bearer {}'.format(access_token)
        headers      = {'Authorization': auth_token}
        url          = '{}pull_foreign_auth'.format(CLOUD_FUNCTIONS_BASE)
        data         = {'resource_name': 'aws_kinesis'}
        resp         = requests.post(url, json=data, headers=headers)

        if resp.status_code == 200:
            data = resp.json()
            self.ACCESS_KEY = data['keys']['access_key']
            self.SECRET_KEY = data['keys']['secret_key']
            self.stream     = data['keys']['arn']
            self.expiration = data['expiration']
            self.authorized = True
            return True
        else:
            self.authorized = False
            return False

    def validate_expiry(self):
        print('is expired: ', self.expiration < ms_timestamp())
        if self.expiration < ms_timestamp():
            self.authorize()

    def _connect_client(self):
        """ Connect to Kinesis Streams """
        did_authorize = False
        if not self.expiration or ms_timestamp() > self.expiration:
            did_authorize = self.authorize()
        else:
            did_authorize = True

        if did_authorize:
            self.CLIENT = boto3.client('kinesis',
                                region_name=self.REGION_NAME,
                                aws_access_key_id=self.ACCESS_KEY,
                                aws_secret_access_key=self.SECRET_KEY)
            self.authorized = True
            return self.CLIENT
        
        else:
            return False

    def send_stream(self, data, partition_key=None):
        if not self.authorized:
            return 'Service not authorized', 403

        # If no partition key is given, assume random sharding for even shard write load
        if partition_key == None:
            partition_key = uuid.uuid4()

        client = self._connect_client()
        if client:
            client.put_record(
                StreamARN=self.stream,
                Data=json.dumps(data),
                PartitionKey=str(partition_key)
            )
            return True
        else:
            return False

    def get_auth_token(self):
        access_token_obj = util_ref.find_one({'type': 'access_token'}, {'_id': 0})
        access_token = access_token_obj['token']
        return access_token