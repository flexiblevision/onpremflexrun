"""Time Machine install, uninstall, and record cleanup.

The OCR toggle and status probe that used to live here moved to
addon_routes.py, which drives them from addons/catalog/ocr/addon.json. They were
only here because OCR had nowhere else to go.
"""
import os
from flask import request
from flask_restx import Resource
import auth
from redis import Redis
from rq import Queue, Retry
from worker_scripts.job_manager import insert_job
from timemachine.installer import local_zip_push_install, cloud_install, validate_account
from timemachine.cleanup import cleanup_timemachine_records

redis_con = Redis('localhost', 6379, password=None)
job_queue = Queue('default', connection=redis_con)

class EnableTimemachine(Resource):
    @auth.requires_auth
    def post(self):
        j = request.json
        access_token = request.headers.get('Access-Token')
        if not access_token: return 'Access-Token header is required', 403

        tm_types = ['local', 'cloud', 'zip_push']
        did_install = False
        authorized = validate_account('time_machine', access_token)
        if not authorized: return 'Account is not authorized to use the Time Machine feature.', 403
        if 'type' in j:
            if j['type'] in tm_types:
                if j['type'] == 'local' or j['type'] == 'zip_push':
                    install_job = job_queue.enqueue(
                                    local_zip_push_install,
                                    j['type'],
                                    job_timeout=600,
                                    result_ttl=3600,
                                    retry=Retry(max=5, interval=60),
                                )
                    job = insert_job(install_job.id, 'installing time machine locally')
                    did_install = True
                else:
                    did_install = cloud_install()
            else:
                return 'type must be one of the following: [local, cloud, zip_push]', 500
        else:
            return 'missing type key. Type key must be passed',500

        if did_install:
            return True, 200
        else:
            return False, 500

class DisableTimemachine(Resource):
    @auth.requires_auth
    def delete(self):
        j = request.json
        tm_types = ['local', 'cloud', 'zip_push']
        if 'type' in j:
            if j['type'] == 'local' or j['type'] == 'zip_push':
                os.system('sh '+os.environ['HOME']+'/flex-run/system_server/timemachine/uninstaller.sh')
            else:
                print('uninstall cloud timemachine')
            return True, 200
        else:
            return 'missing type key. Type key must be passed',500

class CleanupTimemachine(Resource):
    @auth.requires_auth
    def delete(self):
        return cleanup_timemachine_records(), 200

def register_routes(api):
    api.add_resource(EnableTimemachine, '/enable_timemachine')
    api.add_resource(DisableTimemachine, '/disable_timemachine')
    api.add_resource(CleanupTimemachine, '/cleanup_timemachine')
