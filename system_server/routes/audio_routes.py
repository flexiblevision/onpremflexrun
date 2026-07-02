import os
import requests
from flask import request
from flask_restx import Resource
import auth
from redis import Redis
from rq import Queue, Retry
from worker_scripts.job_manager import insert_job, enable_audio

redis_con = Redis('localhost', 6379, password=None)
job_queue = Queue('default', connection=redis_con)

# HTTP port the audio-anomaly service (FVKWS/audio_anomaly server.py) listens on.
AUDIO_HTTP_PORT = 5702
AUDIO_CONTAINER = 'audio-anomaly'


class ManageAudioDevices(Resource):
    @auth.requires_auth
    def put(self):
        j = request.json
        if 'state' in j:
            if j['state']:
                install_job = job_queue.enqueue(
                    enable_audio,
                    job_timeout=600,
                    result_ttl=3600,
                    retry=Retry(max=5, interval=60),
                )
                job = insert_job(install_job.id, 'installing and deploying audio devices service')
                return 'enabling...', 200
            else:
                os.system(f"docker stop {AUDIO_CONTAINER}")
                os.system(f"docker rm {AUDIO_CONTAINER}")
                return 'disabled', 200

        return 'state key not found', 404


class AudioDevicesStatus(Resource):
    def get(self):
        # /api/audio/devices always returns 200 (JSON list) when the service is up.
        # (There is no "/" route — server.py serves its UI at "/device".)
        try:
            res = requests.get(f'http://172.17.0.1:{AUDIO_HTTP_PORT}/api/audio/devices', timeout=2)
            return res.status_code == 200
        except Exception as error:
            print(error)
            return False


def register_routes(api):
    api.add_resource(ManageAudioDevices, '/manage_audio_devices')
    api.add_resource(AudioDevicesStatus, '/audio_devices_status')
