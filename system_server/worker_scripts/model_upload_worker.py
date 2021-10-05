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
lite_model_types  = ['high_speed']


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

def read_job_file(temp_path):
    ver_path = ""
    directory_contents = os.listdir(temp_path)
    for item in directory_contents:
        if os.path.isdir(temp_path+'/'+item):
            ver_path = item
            break

    job_path = temp_path+'/'+ver_path+'/job.json'
    if os.path.exists(job_path):
        with open(job_path) as f:
            data = json.load(f)
            if data:
                return data

    return {}

def upload_model(temp_model_path, filename):
    if os.path.exists(temp_model_path) and filename:
        job_data    = read_job_file(temp_model_path)            
        split_fname = filename.split('#')
        model_name  = split_fname[0]

        if 'model_version' in job_data:
            version = str(job_data['model_version'])
        else:
            version = split_fname[1].split('.')[0]

        model_path     = '/models/'+model_name
        version_path   = '/models/'+model_name+'/'+version
        lite_model_path = '/lite_models/'+model_name
        lite_version_path = lite_model_path + '/' + version

        model_exists   = os.path.exists(model_path)
        version_exists = os.path.exists(version_path)
        lite_model_exists = os.path.exists(lite_model_path)
        lite_version_exists = os.path.exists(lite_version_path)

        model_type = job_data['model_type'] if 'model_type' in job_data else 'high_accuracy'
        is_lite_model = False

        if model_type in lite_model_types:
            model_path    = lite_model_path
            model_exists  = lite_model_exists
            is_lite_model = True            

        if model_exists and version_exists:
            print(model_type, ' ', model_name, ' ', version, ' already exists')
            os.system('rm -rf '+temp_model_path)
            return False

        #read model information from job.json to find the model type
        
        if model_exists and not version_exists:
            print('ADDING VERSION')
            #ADD VERSION TO ALREADY EXISTING MODEL FOLDER - DONT RECREATE CONFIG FILE
            os.system("mv {}/{} {}".format(temp_model_path, str(version), model_path))

            if is_lite_model:
                models_collection.update_one({'type': model_name}, {'$push': {'high_speed': version}})
            else:
                #ADD VERSION TO MONGO DB MODEL LIST
                models_collection.update_one({'type': model_name}, {'$push': {'versions': version}})
        else:
            print('ADDING MODEL AND VERSION')
            #ADD MODEL AND VERSION
            os.system("mkdir "+model_path)
            os.system("mv {}/{} {}".format(temp_model_path, str(version), model_path))

            if is_lite_model:
                models_collection.update_one({'type': model_name}, {'$set': {'high_speed': [version]} }, True)
            else:
                #ADD MODEL AND VERSION TO DB
                models_collection.update_one({'type': model_name}, {'$set': {'versions': [version]} }, True)

                #GENERATE CONFIG FILE
                create_config_file()

        if is_lite_model:
            print('PUSHING MODELS TO PREDICT LITE SERVER')
            os.system("docker cp "+lite_version_path+" predictlite:/data/models/")
            os.system('rm -rf '+temp_model_path)
        else:
            print('PUSHING MODELS TO PREDICTION SERVER')
            os.system("docker cp /models localprediction:/")
            os.system("docker restart localprediction")
            os.system('rm -rf '+temp_model_path)
        
        return True
    else:
        return False
