import os
import subprocess
import requests
from flask import request
from flask_restx import Resource
from pymongo import MongoClient
import auth
import settings
from redis import Redis
from rq import Queue, Retry
from worker_scripts.job_manager import insert_job, push_analytics_to_cloud
from timemachine.zip_push import push_event_records, get_unprocessed_events
from helpers.config_helper import write_settings_to_config

client = MongoClient("172.17.0.1")
utils_db = client["fvonprem"]["utils"]

redis_con = Redis('localhost', 6379, password=None)
job_queue = Queue('default', connection=redis_con)

MAX_JOBS = 1000
CLOUD_DOMAIN = settings.config['cloud_domain'] if 'cloud_domain' in settings.config else "https://clouddeploy.api.flexiblevision.com"

class AuthToken(Resource):
    @auth.requires_auth
    def get(self):
        cmd = subprocess.Popen(['cat', os.environ['HOME']+'/flex-run/system_server/creds.txt'], stdout=subprocess.PIPE)
        cmd_out, cmd_err = cmd.communicate()
        cleanStr = cmd_out.strip().decode("utf-8")
        if cleanStr: return cleanStr

    @auth.requires_auth
    def post(self):
        j = request.json
        if j:
            if 'obj' in j and 'server_ip' in j['obj']:
                settings.config['cloud_domain'] = 'http://{}'.format(j['obj']['server_ip'])
                write_settings_to_config()
            os.system('echo '+j['refresh_token']+' > '+os.environ['HOME']+'/flex-run/system_server/creds.txt')
            return True
        return False

class DeAuthorize(Resource):
    @auth.requires_auth
    def get(self):
        os.system("rm "+os.environ['HOME']+"/flex-run/system_server/creds.txt")

class SyncAnalytics(Resource):
    @auth.requires_auth
    def get(self):
        access_token = request.headers.get('Access-Token')
        num_jobs = job_queue.count
        if num_jobs > MAX_JOBS:
            return f"MAX JOBS EXCEEDED: {num_jobs}/{MAX_JOBS} - IGNORING SYNC"

        if access_token:
            push_analytics_to_cloud(CLOUD_DOMAIN, access_token)
            events = get_unprocessed_events()
            if events['count'] > 0:
                er_push = job_queue.enqueue(push_event_records, CLOUD_DOMAIN, access_token,
                            events,
                            job_timeout=1800,
                            result_ttl=3600,
                            retry=Retry(max=5, interval=60),
                        )

                if er_push: insert_job(er_push.id, 'Pushing '+str(events['count'])+' events to cloud')

class SyncFlow(Resource):
    @auth.requires_auth
    def get(self):
        access_token = request.headers.get('Access-Token')
        flow_path = "{}/flows.json".format(os.environ['HOME'])
        os.system("docker cp nodecreator:/root/.node-red/flows.json "+flow_path)
        dev_ref = utils_db.find_one({'type':'device_id'})
        device_id = None if not dev_ref else dev_ref['id']

        if not device_id: return 'device id not found', 404

        url = '{}/api/capture/devices/{}/flow'.format(CLOUD_DOMAIN, device_id)
        headers = {'Authorization' : 'Bearer {}'.format(access_token), 'Accept' : 'application/json', 'Content-Type' : 'application/json'}
        r = requests.post(url, data=open(flow_path, 'rb'), headers=headers)
        return r.text, r.status_code

def register_routes(api):
    api.add_resource(AuthToken, '/auth_token')
    api.add_resource(DeAuthorize, '/deauthorize')
    api.add_resource(SyncAnalytics, '/sync_analytics')
    api.add_resource(SyncFlow, '/sync_flow')
