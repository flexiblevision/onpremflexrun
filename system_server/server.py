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
#sys.path.append("/home/fvonprem/Tensorflow/models/research")
#from object_detection.utils import label_map_util

app = Flask(__name__)
api = Api(app)

CORS(app)
NUM_CLASSES         = 99

def base_path():
    #mounted memory to ssd
    xavier_ssd = '/xavier_ssd/'
    return xavier_ssd if os.path.exists(xavier_ssd) else '/'
BASE_PATH_TO_MODELS = base_path()+'models/'

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
        print('upgrading system')
        os.system("sh ./upgrade_system.sh")
        return True

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
        #label_map            = label_map_util.load_labelmap(path_to_model_labels)
        #categories           = label_map_util.convert_label_map_to_categories(label_map, max_num_classes=NUM_CLASSES, use_display_name=True)
        #category_index       = label_map_util.create_category_index(categories)
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

class SystemVersion(Resource):
    def get(self): 
        cmd = subprocess.Popen(['docker', 'inspect', "--format='{{.Config.Image}}'", 'capdev'], stdout=subprocess.PIPE)
        cmd_out, cmd_err = cmd.communicate()
        data = cmd_out.strip().decode("utf-8").split(':')[1].replace("'", "")
        return data

api.add_resource(AuthToken, '/auth_token')
api.add_resource(Networks, '/networks')
api.add_resource(Shutdown, '/shutdown')
api.add_resource(Restart, '/restart')
api.add_resource(Upgrade, '/upgrade')
api.add_resource(CategoryIndex, '/category_index/<string:model>/<string:version>')
api.add_resource(Models, '/models')
api.add_resource(PushModels, '/push_models')
api.add_resource(DownloadModels, '/download_models')
api.add_resource(SystemVersion, '/system_version')

if __name__ == '__main__':
     app.run(host='0.0.0.0',port='5001')
