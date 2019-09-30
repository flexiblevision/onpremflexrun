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

from flask_cors import CORS
from flask import jsonify
from pathlib import Path

import os
import sys
import zipfile
import base64
import io
import time
from collections import defaultdict
from io import StringIO
from io import BytesIO

app = Flask(__name__)
api = Api(app)

CORS(app)
NUM_CLASSES = 99
CONTAINERS  = ['capdev', 'captureui', 'localprediction']

def base_path():
    #mounted memory to ssd
    xavier_ssd = '/xavier_ssd/'
    return xavier_ssd if os.path.exists(xavier_ssd) else '/'
BASE_PATH_TO_MODELS = base_path()+'models/'

def get_current_container_version(container):
    cmd = subprocess.Popen(['docker', 'inspect', "--format='{{.Config.Image}}'", container], stdout=subprocess.PIPE)
    cmd_out, cmd_err = cmd.communicate()
    data = cmd_out.strip().decode("utf-8").split(':')[1].replace("'", "")
    return data

def parse_latest_tag(repo_tags, container):
    tags = []
    for repo in repo_tags['results']: 
        if repo['name'] != 'latest': tags.append(float(repo['name']))

    latest_version = sorted(tags,key=float,reverse=True)[0]
    system_version = get_current_container_version(container)
    return latest_version == system_version

def is_container_uptodate(container):
    repo_tags = requests.get('https://registry.hub.docker.com/v2/repositories/fvonprem/arm-'+container+'/tags/').json()
    return parse_latest_tag(repo_tags, container)

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
        cap_uptd   = is_container_uptodate('capdev')
        capui_uptd = is_container_uptodate('captureui')
        predict_uptd = is_container_uptodate('localprediction')
        os.system("sh ./upgrade_system.sh cap_uptd capui_uptd prdict_uptd")

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
        path_to_model_labels = BASE_PATH_TO_MODELS + model + '/' + version + '/labels.json' 
        labels = None
        with open(path_to_model_labels) as data:
            labels = json.load(data)

        category_index = {}
        for label in labels:
            label_id = labels[label]
            category_index[label_id] = {"id": label_id, "name": label}

        return category_index

#mock behavior of request route to get models data
# --- will delete ----
class Models(Resource):
    @auth.requires_auth
    def get(self):
        print(request.host)
        models = {
                    'test_model': ['1563658006967'],
                    'bottle_qc': ['1562884137051']
                }
        return models
# --- will delete ---

# mock download models behavior
class DownloadModels(Resource):
    @auth.requires_auth
    def get(self):
        # file_path will be replaced with a request to cloud server
        file_path = base_path()+'models.zip'
        with zipfile.ZipFile(file_path, 'r') as zip_ref:
            zip_ref.extractall(base_path())

class PushModels(Resource):
    @auth.requires_auth
    def get(self):
        os.system("docker cp "+base_path()+"models localprediction:/")
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
        return all([is_container_uptodate('capdev'), is_container_uptodate('captureui'), is_container_uptodate('localprediction')])


api.add_resource(AuthToken, '/auth_token')
api.add_resource(Networks, '/networks')
api.add_resource(Shutdown, '/shutdown')
api.add_resource(Restart, '/restart')
api.add_resource(Upgrade, '/upgrade')
api.add_resource(CategoryIndex, '/category_index/<string:model>/<string:version>')
api.add_resource(Models, '/models')
api.add_resource(PushModels, '/push_models')
api.add_resource(DownloadModels, '/download_models')
api.add_resource(SystemVersions, '/system_versions')
api.add_resource(SystemIsUptodate, '/system_uptodate')

if __name__ == '__main__':
     app.run(host='0.0.0.0',port='5001')
