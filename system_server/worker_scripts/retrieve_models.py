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

client            = MongoClient("172.18.0.1",authMechanism='SCRAM-SHA-256')
job_collection    = client["fvonprem"]["jobs"]
models_collection = client["fvonprem"]["models"]

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
    if os.path.exists(BASE_PATH_TO_MODELS): os.system("rm -rf "+BASE_PATH_TO_MODELS)
    os.system("mkdir "+BASE_PATH_TO_MODELS)

    models_versions = []
    for model_ref in data:
        project_id   = model_ref['_id']
        model_name   = model_ref['type']
        versions     = model_ref['versions']
        model_folder = BASE_PATH_TO_MODELS + model_name
        if len(versions) > 0: os.system("mkdir " + model_folder)

        model_data = {'type': model_name, 'versions': []}
        #iterate over the models data and request/extract model to models folder
        for version in versions:
            path = 'http://104.154.128.121/api/capture/models/download/'+str(project_id)+'/'+str(version) 
            res = os.system(f"curl -X GET {path} -H 'accept: application/json' -H 'Authorization: Bearer {token}' -o {model_folder}/model.zip")
            try: 
                with zipfile.ZipFile(model_folder+'/model.zip') as zf:
                    zf.extractall(model_folder)
                os.system("mv "+model_folder+"/job.json "+model_folder+"/"+str(version))
                os.system("mv "+model_folder+"/object-detection.pbtxt "+model_folder+"/"+str(version))
                model_data['versions'].append(version)
            except zipfile.BadZipfile:
                print('bad zipfile in '+model_folder)

            os.system("rm -rf "+model_folder+'/model.zip')

        if model_data['versions']: models_versions.append(model_data)
    
    if models_versions:
        create_config_file(models_versions)
        save_models_versions(models_versions)
        print('removing old models')
        os.system("docker exec localprediction rm -rf /models")
        print('pushing new models to localprediction')
        os.system("docker cp "+base_path()+"models localprediction:/")
        delete_job_ref(job_id)
        return True
    else: 
        failed_job_ref(job_id)
        return False
        

def save_models_versions(models_versions):
    models_collection.drop()
    models_collection.insertMany(models_versions)

def insert_job_ref(job_id):
    job_collection.insert({
        '_id': job_id,
        'type': 'model_download',
        'start_time': str(datetime.datetime.now()),
        'status': 'downloading'
        })

def delete_job_ref(job_id):
    query = {'_id': job_id}
    job_collection.deleteOne(query)


def failed_job_ref(job_id):
    query = {'_id': job_id}
    job_collection.updateOne(query, {$set: {'status': 'failed'}})
