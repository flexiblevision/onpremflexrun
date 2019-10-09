import requests
import os
import sys
import zipfile, io
import base64
import io
import time
from collections import defaultdict
from io import StringIO
from io import BytesIO

def base_path():
    xavier_ssd = '/xavier_ssd/'
    return xavier_ssd if os.path.exists(xavier_ssd) else '/'

BASE_PATH_TO_MODELS = base_path()+'models/'

def create_config_file(data):
    with open (BASE_PATH_TO_MODELS+'model.config', 'a') as f:
        f.write('model_config_list {\n')
        for model in data:
            if len(model['versions']) > 0:
                f.write('\tconfig {\n')
                f.write('\t\tname: \''+model['type']+'\'\n')
                f.write('\t\tbase_path: \''+'/models/'+model['type']+'/\'\n')
                f.write('\t\tmodel_platform: \'tensorflow\'\n')
                f.write('\t\tmodel_version_policy: {all {}}\n')
                f.write('\t}\n')
        f.write('}')

def retrieve_models(data):
    if os.path.exists(BASE_PATH_TO_MODELS): os.system("rm -rf "+BASE_PATH_TO_MODELS)
    os.system("mkdir "+BASE_PATH_TO_MODELS)

    #first create the config file from the models data
    create_config_file(data)

    for model_ref in data:
        project_id   = model_ref['_id']
        model_name   = model_ref['type']
        versions     = model_ref['versions']
        model_folder = BASE_PATH_TO_MODELS + model_name
        if len(versions) > 0: os.system("mkdir " + model_folder)

        #second iterate over the models data and request/extract model to models folder
        for version in versions:

            path = 'http://104.154.128.121/api/capture/models/download/'+str(project_id)+'/'+str(version)
            #path = 'http://104.154.128.121/api/capture/models/download/c1948ab7-4e7c-48d1-810f-969328c90c23/1570413180585'

            token = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIsImtpZCI6IlJqTTRRa1kyTURnd1JrRTBNREZCT1VKR01UVkRNa0ZFTXpNMk9EVXdPRGxGUkRWRFJqZEVNZyJ9.eyJpc3MiOiJodHRwczovL2ZsZXhpYmxldmlzaW9uLmF1dGgwLmNvbS8iLCJzdWIiOiJhdXRoMHw1ZDlhMjMyNTQ5ZGIxYzBjNjM2Y2YwY2YiLCJhdWQiOiJodHRwczovL2ZsZXhpYmxldmlzaW9uL2FwaSIsImlhdCI6MTU3MDYzOTAyMiwiZXhwIjoxNTcwNzI1NDIyLCJhenAiOiI0dHpqWk5qRnl0RHE5S3FpRWg2MFpweVZ4UHF5dkRoSCIsInBlcm1pc3Npb25zIjpbXX0.sbqIFGkiWK1o3sBbwUccIy9oq_2WYy-LP4H-h3zsFeqAYg1dCDYBivhl52fx-3hRrcLwnPmKFelyR656HqMbbEdlqjmXSmm1UMabayeDxgCh0HLENGo7bBYWe3CXkYJr28Qroh5kSIJkyc7bRZIe8WPRnwsKkFb15QIrpkGTAAoErQAk1vP8GgwvjdbOZLOX90PxpumnYTBHE1W42bDQr1ocuQnKZXcKvkqc-grm6R5xJuglXRR6heLWY_csSlNxDjaDW2AhR9haOb9sqwV18ZxZhB-39wzmCr-Zsj5GjPmtd7hQo_bpwVgIEHqqtT2oxb1RBQaZlr7ZYV2JRFz-Vg"

            res = os.system(f"curl -X GET {path} -H 'accept: application/json' -H 'Authorization: Bearer {token}' -o {model_folder}/model.zip")
            
            print(res)
            print('-----model download response-------------')
            if res: print('yes we have a response')
            
            try: 
                with zipfile.ZipFile(model_folder+'/model.zip') as zf:
                    zf.extractall(model_folder)
                
                os.system("ls "+model_folder)
                os.system("mv "+model_folder+"/job.json "+model_folder+"/"+str(version))
                os.system("mv "+model_folder+"/object-detection.pbtxt "+model_folder+"/"+str(version))
            except zipfile.BadZipfile:
                print('bad zipfile in '+model_folder)

            os.system("rm -rf "+model_folder+'/model.zip')
            os.system("ls "+model_folder)

    print('removing old models')
    os.system("docker exec localprediction rm -rf /models")
    print('pushing new models to localprediction')
    os.system("docker cp "+base_path()+"models localprediction:/")
    return True
        



