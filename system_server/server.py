from datetime import timedelta
from flask import make_response, request, current_app
from functools import update_wrapper

from flask import Flask
from flask_restful import Resource, Api
import json
from json import dumps
import subprocess
import os
import datetime
import auth
import requests
import re
import uuid

from flask_cors import CORS
from flask import jsonify
from pathlib import Path

import os
import sys
import zipfile, io
import base64
import io
import time
from collections import defaultdict
from io import StringIO
from io import BytesIO
from version_check import *
from worker_scripts.retrieve_models import retrieve_models

from redis import Redis
from rq import Queue, Worker, Connection
from rq.job import Job

app = Flask(__name__)
api = Api(app)

CORS(app)
NUM_CLASSES = 99
redis_con   = Redis('localhost', 6379, password=None)
job_queue   = Queue('default', connection=redis_con)
CONTAINERS  = {'backend':'capdev', 'frontend':'captureui', 'prediction':'localprediction'}

def base_path():
    #mounted memory to ssd
    xavier_ssd = '/xavier_ssd/'
    return xavier_ssd if os.path.exists(xavier_ssd) else '/'

BASE_PATH_TO_MODELS = base_path()+'models/'

def system_info():
    out = subprocess.Popen(['lshw', '-short'], stdout=subprocess.PIPE)
    cmd = subprocess.Popen(['grep', 'system'], stdin=out.stdout, stdout=subprocess.PIPE)
    cmd_out, cmd_err = cmd.communicate()
    system = cmd_out.strip().decode("utf-8")
    return system

def system_arch():
    cmd = subprocess.Popen(['arch'], stdout=subprocess.PIPE)
    cmd_out, cmd_err = cmd.communicate()
    return  cmd_out.strip().decode("utf-8")

class Shutdown(Resource):
    @auth.requires_auth
    def get(self):
        print('shutting down system')
        os.system("shutdown -h now")
        return True

class Restart(Resource):
    @auth.requires_auth
    def get(self):
        print('restarting system')
        os.system("shudown -r now")
        return True

class Upgrade(Resource):
    @auth.requires_auth
    def get(self):
        cap_uptd     = is_container_uptodate('backend')[1]
        capui_uptd   = is_container_uptodate('frontend')[1]
        predict_uptd = is_container_uptodate('prediction')[1] 
        
        os.system("chmod +x ./upgrade_system.sh")
        os.system("sh ./upgrade_system.sh "+cap_uptd+" "+capui_uptd+" "+predict_uptd)

class AuthToken(Resource):
    @auth.requires_auth
    def get(self):
        cmd = subprocess.Popen(['cat', 'creds.txt'], stdout=subprocess.PIPE)
        cmd_out, cmd_err = cmd.communicate()
        cleanStr = cmd_out.strip().decode("utf-8")
        if cleanStr: return cleanStr

    @auth.requires_auth
    def post(self):
        j = request.json
        if j:
            os.system('echo '+j['refresh_token']+' > creds.txt')
            return True
        return False

class Networks(Resource):
    def get(self):
        networks = subprocess.check_output(['nmcli', '-f', 'SSID', 'dev', 'wifi'])
        nets = {}
        for i,line in enumerate(networks.splitlines()): nets[i] = line.decode('utf-8')
        return nets

    @auth.requires_auth
    def post(self):
        j = request.json
        return os.system("nmcli dev wifi connect "+j['netName']+" password "+j['netPassword'])

class CategoryIndex(Resource):
    def get(self, model, version):
        # category index will now be created from the job.json file 
        # read in json file and parse the labelmap_dict to create the category_index 

        path_to_model_labels = BASE_PATH_TO_MODELS + model + '/' + version + '/job.json' 
        labels = None
        with open(path_to_model_labels) as data:
            labels = json.load(data)['labelmap_dict']
        
        category_index = {}
        for key in labels.keys():
            _id = labels[key]
            category_index[_id] = {"id": _id, "name": key}

        return category_index

class DownloadModels(Resource):
    @auth.requires_auth
    def post(self):
        data           = request.json 
        access_token   = request.headers.get('Access-Token')
        job_queue.enqueue(retrieve_models, data, access_token, job_timeout=-1, result_ttl=-1)
        return True 

class SystemVersions(Resource):
    def get(self):
        backend_version    = get_current_container_version('capdev')
        frontend_version   = get_current_container_version('captureui')
        prediction_version = get_current_container_version('localprediction')
        return {'backend_version': backend_version,
                'frontend_version': frontend_version,
                'prediction_version': prediction_version
                }

class SystemIsUptodate(Resource):
    def get(self):
        return all([is_container_uptodate('backend')[0], is_container_uptodate('frontend')[0], is_container_uptodate('prediction')[0]])

class DeviceInfo(Resource):
    def get(self):
        domain = request.headers.get('Host').split(':')[0]
        info = {}
        info['system']        = system_info()
        info['arch']          = system_arch()
        info['mac_id']        = ':'.join(re.findall('..', '%012x' % uuid.getnode()))
        info['last_active']   = str(datetime.datetime.now())
        info['last_known_ip'] = domain
        return info

class SaveImage(Resource):
    @auth.requires_auth
    def post(self):
        data = request.json
        path = os.environ['HOME']+'/'+'stored_images'
        if not os.path.exists(path):
            os.system('mkdir '+path)
        if 'img' in data:
            img_path   = path+'/'+str(uuid.uuid4())+'.jpg'
            decode_img = base64.b64decode(data['img'])
            with open(img_path, 'wb') as fh:
                fh.write(decode_img)


api.add_resource(AuthToken, '/auth_token')
api.add_resource(Networks, '/networks')
api.add_resource(Shutdown, '/shutdown')
api.add_resource(Restart, '/restart')
api.add_resource(Upgrade, '/upgrade')
api.add_resource(CategoryIndex, '/category_index/<string:model>/<string:version>')
api.add_resource(DownloadModels, '/download_models')
api.add_resource(SystemVersions, '/system_versions')
api.add_resource(SystemIsUptodate, '/system_uptodate')
api.add_resource(DeviceInfo, '/device_info')
api.add_resource(SaveImage, '/save_img')

if __name__ == '__main__':
     app.run(host='0.0.0.0',port='5001')
