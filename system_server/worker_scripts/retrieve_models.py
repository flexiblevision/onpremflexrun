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

settings_path = os.environ['HOME']+'/flex-run'
sys.path.append(settings_path)
import settings

client             = MongoClient("172.17.0.1")
job_collection     = client["fvonprem"]["jobs"]
models_collection  = client["fvonprem"]["models"]
presets_collection = client["fvonprem"]["io_presets"]

CLOUD_DOMAIN = settings.config['cloud_domain'] if 'cloud_domain' in settings.config else "https://clouddeploy.api.flexiblevision.com"

def base_path():
    xavier_ssd = '/xavier_ssd/'
    return xavier_ssd if os.path.exists(xavier_ssd) else '/'

BASE_PATH_TO_MODELS = base_path()+'models/'
BASE_PATH_TO_LITE_MODELS = base_path()+'lite_models/'
LITE_MODEL_TYPES    = ['high_speed']

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

def download_by_link(token, project_id, version, destination):
    # get link 
    path = CLOUD_DOMAIN+'/api/capture/models/download_link/'+str(project_id)+'/'+str(version)
    headers = {'Authorization': 'Bearer '+token}
    res  = requests.get(path, headers=headers)
    
    signed_link = res.json()
    written     = 0
    chunk_size  = 8192
    download = requests.get(signed_link, stream=True)
    cont_length = download.headers['Content-length']
    with download as r:
        r.raise_for_status()
        with open(destination, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
                written += chunk_size
                print(f"{written}/{cont_length}")
    

def retrieve_models(data, token):
    BASE_PATH_TO_MODELS = base_path()+'models/'
    BASE_PATH_TO_LITE_MODELS = base_path()+'lite_models/'
    LITE_MODEL_TYPES    = ['high_speed']


    model_type = 'versions' if 'model_type' not in data or data['model_type'] != 'high_speed' else data['model_type']
    if 'model_type' in data and data['model_type'] == 'ocr': model_type = 'ocr' 
    
    if model_type == 'ocr':
        BASE_PATH_TO_MODELS = '/tmp/'
    elif model_type not in LITE_MODEL_TYPES:
        #if model.config exists - remove it
        if os.path.exists(BASE_PATH_TO_MODELS+'model.config'):
            os.system("rm -rf "+BASE_PATH_TO_MODELS+'model.config')
    else:
        # update base models path
        BASE_PATH_TO_MODELS = BASE_PATH_TO_LITE_MODELS

    #check if exclude_models is empty
    if not bool(data['exclude_models']) and os.path.exists(BASE_PATH_TO_MODELS): 
        os.system("rm -rf "+BASE_PATH_TO_MODELS)
        os.system("mkdir "+BASE_PATH_TO_MODELS)

    if not os.path.exists(BASE_PATH_TO_MODELS): 
        os.system("mkdir "+BASE_PATH_TO_MODELS)

    models_versions = {}
    if 'models' not in data or not data['models'].values():
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

        model_data = {'type': model_name}
        model_data[model_type] =[]

        #iterate over the models data and request/extract model to models folder
        for version in versions:
            if model_name in exclude_models and version in exclude_models[model_name]:
                # model has already been downloaded
                if os.path.exists(model_folder+'/'+str(version)):
                    model_data[model_type].append(version)
                    print('model has already been downloaded')
                else:
                    print('version not found, skipping...', model_folder+'/'+str(version))
            else:
                print('Syncing '+model_name+' versions '+str(version))
                if model_type == 'ocr':
                    download_by_link(token, str(project_id), str(version), f"{model_folder}/model.zip")
                else:
                    path = CLOUD_DOMAIN+'/api/capture/models/download/'+str(project_id)+'/'+str(version)
                    res = os.system(f"curl -X GET {path} -H 'accept: application/json' -H 'Authorization: Bearer {token}' -o {model_folder}/model.zip")
                
                if os.path.exists(model_folder+'/model.zip'):
                    try:
                        with zipfile.ZipFile(model_folder+'/model.zip') as zf:
                            zf.extractall(model_folder)
                        os.system("mv "+model_folder+"/job.json "+model_folder+"/"+str(version))
                        os.system("mv "+model_folder+"/object-detection.pbtxt "+model_folder+"/"+str(version))

                        #move pb file to model version directory
                        model_file_path = model_folder+"/"+str(version)+"/saved_model/saved_model.pb"
                        if os.path.exists(model_file_path):
                            os.system("mv "+model_file_path+" "+model_folder+"/"+str(version))

                        #move variables folder to model version directory
                        vars_path = model_folder+"/"+str(version)+"/saved_model/variables"
                        if os.path.exists(vars_path):
                            os.system("mv "+vars_path+" "+model_folder+"/"+str(version))

                        model_data[model_type].append(version)
                    except zipfile.BadZipfile:
                        print('bad zipfile in '+model_folder)
                        
                    os.system("rm -rf "+model_folder+'/model.zip')

        if model_data[model_type]:
            if model_name in models_versions:
                models_versions[model_name][model_type] += model_data[model_type]
            else:
                models_versions[model_name] = model_data

    if models_versions:
        if model_type not in LITE_MODEL_TYPES and model_type != 'ocr':
            create_config_file(models_versions.values())

        save_models_versions(models_versions.values(), model_type)
        
        if model_type == 'ocr':
            #push model into ocr continer 
            print('pushing model into ocr container')
            os.system(f"mv {BASE_PATH_TO_MODELS}{model_name} {BASE_PATH_TO_MODELS}ocrmodel")


        elif model_type in LITE_MODEL_TYPES:
            os.system("docker exec predictlite rm -rf /data/lite_models")
            print('pushing models into predictlite server: ', BASE_PATH_TO_MODELS)
            os.system("docker cp "+BASE_PATH_TO_MODELS+" predictlite:/data/")
        else:
            os.system("docker exec localprediction rm -rf /models")
            print('pushing new models to localprediction')
            os.system("docker cp "+BASE_PATH_TO_MODELS+" localprediction:/")
            os.system("docker restart localprediction")
        return True
    else:
        return False


def save_models_versions(models_versions, model_type):
    # models_collection.drop()
    # models_collection.insert_many(models_versions)
    db_models = models_collection.find()

    # loop over models and set model type(model name) lists to empty
    for model in db_models:
        model_list = {}
        model_list[model_type] = []
        models_collection.update_one({'type': model['type']}, {'$set': model_list}, True)

        # if versions are empty then remove model
        is_empty = []
        if model_type not in models_versions:
            for version_list in model.values():
                if isinstance(version_list, list):
                    is_empty.append(len(version_list)==0)
                
        if all(is_empty):
            models_collection.delete_one({'type': model['type']})

    # loop over model versions and set model versions array by type/model_name
    for mv in models_versions:
        model_list = {}
        model_list[model_type] = mv[model_type]
        model_query = {'type': mv['type']}
        models_collection.update_one(model_query, {'$set': model_list}, True)
        try:
            assign_preset_to_latest_version(mv['type'], mv[model_type], model_type)
        except Exception as error:
            print(error)

def assign_preset_to_latest_version(model, versions, model_type):
    type_map = {'versions': 'high_accuracy', 'high_speed': 'high_speed'}
    versions.sort()
    latest_version = versions[-1]
    presets = presets_collection.find({'modelName': model, 'modelType': type_map[model_type]})
    for preset in presets:
        presets_collection.update({'presetId': preset['presetId']}, {'$set': {'modelVersion': latest_version}})

def format_filename(s):
    valid_chars = "-_.() %s%s" % (string.ascii_letters, string.digits)
    filename = ''.join(c for c in s if c in valid_chars)
    filename = filename.replace(' ','_')
    return filename
