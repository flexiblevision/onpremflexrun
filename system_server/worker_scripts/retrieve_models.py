import requests
import os
import sys
import zipfile, io
import base64
import io
import time
import uuid
from collections import defaultdict
from io import StringIO
from io import BytesIO
from pymongo import MongoClient
import datetime
import string

client            = MongoClient("172.17.0.1")
job_collection    = client["fvonprem"]["jobs"]
models_collection = client["fvonprem"]["models"]

CLOUD_DOMAIN = "https://clouddeploy.api.flexiblevision.com"
cloud_path   = os.path.expanduser('~/flex-run/setup_constants/cloud_domain.txt')
with open(cloud_path, 'r') as file: 
    CLOUD_DOMAIN = file.read().replace('\n', '')

def base_path():
    xavier_ssd = '/xavier_ssd/'
    return xavier_ssd if os.path.exists(xavier_ssd) else '/'

BASE_PATH_TO_MODELS = base_path()+'models/'

def create_config_file(data):
    with open (BASE_PATH_TO_MODELS+'model.config', 'a') as f:
        f.write('model_config_list {\n')
        for model_data in data:
            f.write('\tconfig {\n')
            f.write('\t\tname: \''+model_data['type']+'\'\n')
            f.write('\t\tbase_path: \''+'/models/'+model_data['type']+'/\'\n')
            f.write('\t\tmodel_platform: \'tensorflow\'\n')
            f.write('\t\tmodel_version_policy: {all {}}\n')
            f.write('\t}\n')
        f.write('}')

def retrieve_models(data, token):
    job_id = str(uuid.uuid4())
    insert_job_ref(job_id)

    #if model.config exists - remove it
    if os.path.exists(BASE_PATH_TO_MODELS+'model.config'):
        os.system("rm -rf "+BASE_PATH_TO_MODELS+'model.config')

    #check if exclude_models is empty
    if not bool(data['exclude_models']) and os.path.exists(BASE_PATH_TO_MODELS): 
        os.system("rm -rf "+BASE_PATH_TO_MODELS)
        os.system("mkdir "+BASE_PATH_TO_MODELS)

    if not os.path.exists(BASE_PATH_TO_MODELS): 
        os.system("mkdir "+BASE_PATH_TO_MODELS)

    models_versions = {}
    if 'models' not in data or not data['models'].values():
        failed_job_ref(job_id)
        return False

    models         = data['models']
    exclude_models = data['exclude_models']
    for model_ref in models.values():
        project_id   = model_ref['_id']
        model_name   = format_filename(model_ref['name'])
        versions     = model_ref['models']
        model_folder = BASE_PATH_TO_MODELS + model_name

        #create model folder if it doesnt exist and there are versions available
        if len(versions) > 0 and not os.path.exists(model_folder): 
            os.system("mkdir " + model_folder)

        model_data = {'type': model_name, 'versions': []}

        #iterate over the models data and request/extract model to models folder
        for version in versions:
            if model_name in exclude_models and version in exclude_models[model_name]:
                # model has already been downloaded
                model_data['versions'].append(version)
                print('model has already been downloaded')
            else:
                print('Syncing '+model_name+' versions '+str(version))
                path = CLOUD_DOMAIN+'/api/capture/models/download/'+str(project_id)+'/'+str(version)
                res = os.system(f"curl -X GET {path} -H 'accept: application/json' -H 'Authorization: Bearer {token}' -o {model_folder}/model.zip")
                
                if os.path.exists(model_folder+'/model.zip'):
                    try:
                        with zipfile.ZipFile(model_folder+'/model.zip') as zf:
                            zf.extractall(model_folder)
                        os.system("mv "+model_folder+"/job.json "+model_folder+"/"+str(version))
                        os.system("mv "+model_folder+"/object-detection.pbtxt "+model_folder+"/"+str(version))
                        model_data['versions'].append(version)
                    except zipfile.BadZipfile:
                        print('bad zipfile in '+model_folder)
                        
                    os.system("rm -rf "+model_folder+'/model.zip')

        if model_data['versions']:
            if model_name in models_versions:
                models_versions[model_name]['versions'] += model_data['versions']
            else:
                models_versions[model_name] = model_data

    if models_versions:
        create_config_file(models_versions.values())
        save_models_versions(models_versions.values())
        print('removing old models')
        os.system("docker exec localprediction rm -rf /models")
        print('pushing new models to localprediction')
        os.system("docker cp "+base_path()+"models localprediction:/")
        os.system("docker restart localprediction")
        delete_job_ref(job_id)
        return True
    else:
        failed_job_ref(job_id)
        return False


def save_models_versions(models_versions):
    models_collection.drop()
    models_collection.insert_many(models_versions)

def insert_job_ref(job_id):
    job_collection.insert({
        '_id': job_id,
        'type': 'model_download',
        'start_time': str(datetime.datetime.now()),
        'status': 'running'
        })

def delete_job_ref(job_id):
    query = {'_id': job_id}
    job_collection.drop()
    job_collection.delete_one(query)


def failed_job_ref(job_id):
    query = {'_id': job_id}
    job_collection.update_one(query, {'$set': {'status': 'failed'}})

def format_filename(s):
    valid_chars = "-_.() %s%s" % (string.ascii_letters, string.digits)
    filename = ''.join(c for c in s if c in valid_chars)
    filename = filename.replace(' ','_')
    return filename
