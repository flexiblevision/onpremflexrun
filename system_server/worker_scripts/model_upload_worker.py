import time
import requests
import datetime
from pymongo import MongoClient
import json
from bson import json_util, ObjectId
from collections import defaultdict
import zipfile, io
from io import StringIO
from io import BytesIO
from jose import jwt
import base64
import re
import subprocess
import os
import sys
import uuid


HOST = 'http://172.17.0.1'
PORT = '5000'
HEADERS = {'referer': HOST}

s = requests.Session()
s.headers.update({'referer': HOST})

client            = MongoClient("172.17.0.1")
job_collection    = client["fvonprem"]["jobs"]
models_collection = client["fvonprem"]["models"]


def create_config_file():
    models = models_collection.find()
    data   = json.loads(json_util.dumps(models))
    if not data: return
    with open ('/models/model.config', 'w') as f:
        f.write('model_config_list {\n')
        for model_data in data:
            f.write('\tconfig {\n')
            f.write('\t\tname: \''+model_data['type']+'\'\n')
            f.write('\t\tbase_path: \''+'/models/'+model_data['type']+'/\'\n')
            f.write('\t\tmodel_platform: \'tensorflow\'\n')
            f.write('\t\tmodel_version_policy: {all {}}\n')
            f.write('\t}\n')
        f.write('}')


def upload_model(temp_model_path, filename):
    if os.path.exists(temp_model_path) and filename:
        split_fname    = filename.split('#')
        model_name     = split_fname[0]
        version        = split_fname[1].split('.')[0]
        model_path     = '/models/'+model_name
        version_path   = '/models/'+model_name+'/'+version
        model_exists   = os.path.exists(model_path)
        version_exists = os.path.exists(version_path)

        if model_exists and version_exists:
            print(model_name, ' ', version, ' already exists')
            os.system('rm -rf '+temp_model_path)
            return False
        
        job_id = str(uuid.uuid4())
        job_collection.insert({
            '_id': job_id,
            'type': 'model_upload',
            'start_time': str(datetime.datetime.now()),
            'status': 'running'
        })

        if model_exists and not version_exists:
            print('ADDING VERSION')
            #ADD VERSION TO ALREADY EXISTING MODEL FOLDER - DONT RECREATE CONFIG FILE
            os.system("mv {}/{} {}".format(temp_model_path, str(version), model_path))
            #ADD VERSION TO MONGO DB MODEL LIST
            models_collection.update_one({'type': model_name}, {'$push': {'versions': version}})
        else:
            print('ADDING MODEL AND VERSION')
            #ADD MODEL AND VERSION
            os.system("mkdir /models/"+model_name)
            os.system("mv {}/{} {}".format(temp_model_path, str(version), model_path))

            #ADD MODEL AND VERSION TO DB
            models_collection.update_one({'type': model_name}, {'$set': {'versions': [version]} }, True)

            #GENERATE CONFIG FILE
            create_config_file()


        #PUSH MODELS BACK INTO PREDICTION SERVER
        print('PUSHING MODELS TO PREDICTION SERVER')
        os.system("docker cp /models localprediction:/")
        os.system("docker restart localprediction")
        os.system('rm -rf '+temp_model_path)
        job_collection.delete_one({'_id': job_id})
    else:
        return False
