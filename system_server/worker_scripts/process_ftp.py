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
import subprocess

client            = MongoClient("172.17.0.1")
job_collection    = client["fvonprem"]["jobs"]
util_ref          = client["fvonprem"]["utils"]
directory         = "/home/ftp"
io_ref            = client["fvonprem"]["io_presets"]
ftp_ref           = client["fvonprem"]["ftp_configs"]

def process_img(filename):
    preset     = io_ref.find_one({'ioType': 'FTP'})
    ftp_config = ftp_ref.find_one({'type': 'settings'})

    if preset:
        #add job to database
        img_path = directory+'/'+filename
        if os.path.exists(img_path):
            print('processing image: '+img_path)
            img = subprocess.Popen(['base64', img_path], stdout=subprocess.PIPE)
            img = img.communicate()[0].decode('utf-8').strip()

            if ftp_config:
                if 'usb' in ftp_config and ftp_config['usb']: add_file_to_usb(img)
                if 'predict' in ftp_config and ftp_config['predict']: predict_img(img,filename,preset)

            os.system('rm -rf '+img_path)
            return True, 200
        else:
            return False 
    else:
        return False

def add_file_to_usb(img):
    url     = 'http://172.17.0.1:5001/save_img'
    b64_img = img
    try:
        requests.post(url, json={"img": b64_img})
    except:
        print('Upload to USB failed')
        return False

def predict_img(img, filename, preset):
    #send request to backend predict route
    modelName    = preset['modelName']
    modelVersion = preset['modelVersion']
    presetId     = preset['presetId']
    res     = util_ref.find_one({'type': 'id_token'}, {'_id': 0})
    token   = res['token']
    host    = 'http://172.17.0.1'
    port    = '5000'
    path    = '/api/capture/predict/upload/'+str(modelName)+'/'+str(modelVersion)+'?workstation=ftp_service'+'&preset_id='+str(presetId)
    url     = host+':'+port+path
    headers = {'Authorization': 'Bearer '+ token}
    print('running preset: ', preset)
    try:
        print(filename)
        blb = open(directory+'/'+filename,'rb')
        time.sleep(.4)
        files = {'images': blb}
        res   = requests.post(url, files=files, headers=headers)
        #res  = requests.put(url, headers=headers, json={"src": img, "filename": filename})
        print(res, ' <<<<<<<<<<<<<<<<<')
        return res
    except:
        print('Prediction failed')
        return False
