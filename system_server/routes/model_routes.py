import os
import json
import zipfile
import tempfile
from flask import request
from flask_restx import Resource
from os.path import exists
import auth
from redis import Redis
from rq import Queue, Retry
from worker_scripts.retrieve_models import retrieve_models
from worker_scripts.retrieve_programs import retrieve_programs
from worker_scripts.retrieve_masks import retrieve_masks
from worker_scripts.model_upload_worker import upload_model
from worker_scripts.job_manager import insert_job
from utils.device_utils import base_path

redis_con = Redis('localhost', 6379, password=None)
job_queue = Queue('default', connection=redis_con)

BASE_PATH_TO_MODELS = base_path()+'models/'
BASE_PATH_TO_LITE_MODELS = base_path()+'lite_models/'

for p in [BASE_PATH_TO_MODELS, BASE_PATH_TO_LITE_MODELS]:
    if not os.path.exists(p): os.makedirs(p)

class CategoryIndex(Resource):
    def get(self, model, version):
        model_path = None
        path_to_model_labels = BASE_PATH_TO_MODELS + model + '/' + version + '/job.json'
        path_to_lite_models = BASE_PATH_TO_LITE_MODELS + model + '/' + version + '/job.json'

        model_paths = [path_to_model_labels, path_to_lite_models]
        for path in model_paths:
            if exists(path):
                model_path = path
                break

        if not model_path: return {}

        labels = None
        with open(model_path) as data:
            labels = json.load(data)['labelmap_dict']

        category_index = {}
        for key in labels.keys():
            _id = labels[key]
            category_index[_id] = {"id": _id, "name": key}

        return category_index

class DownloadModels(Resource):
    @auth.requires_auth
    def post(self):
        data = request.json
        access_token = request.headers.get('Access-Token')

        j_models = job_queue.enqueue(retrieve_models, data, access_token,
                                job_timeout=1800,
                                result_ttl=3600,
                                retry=Retry(max=5, interval=60),
                            )
        j_masks = job_queue.enqueue(retrieve_masks, data, access_token,
                                job_timeout=600,
                                result_ttl=3600,
                                retry=Retry(max=5, interval=60),
                            )
        j_progs = job_queue.enqueue(retrieve_programs, data, access_token,
                                job_timeout=600,
                                result_ttl=3600,
                                retry=Retry(max=5, interval=60),
                            )

        if j_models: insert_job(j_models.id, 'Downloading models')
        if j_masks: insert_job(j_masks.id, 'Downloading masks')
        if j_progs: insert_job(j_progs.id, 'Downloading programs')
        return True

class DownloadPrograms(Resource):
    @auth.requires_auth
    def post(self):
        data = request.json
        access_token = request.headers.get('Access-Token')

        j_progs = job_queue.enqueue(retrieve_programs, data, access_token,
                                job_timeout=600,
                                result_ttl=3600,
                                retry=Retry(max=5, interval=60),
                            )
        if j_progs: insert_job(j_progs.id, 'Downloading programs')

        return True

class UploadModel(Resource):
    def post(self):
        fl = request.files['file']
        if not fl: return False

        split_fname = fl.filename.split('#')
        model_name = split_fname[0]

        path = "/"+model_name
        if os.path.exists('/models'+path):
            print(path+' - already exists - REMOVING')
            os.system('rm -rf '+'/models'+path)

        os.system("mkdir "+path)
        fn = tempfile.gettempdir() + 'model.zip'
        fl.save(fn)

        try:
            print('EXTRACTING ZIP FILE')
            with zipfile.ZipFile(fn) as zf:
                zf.extractall(path)

            job_data = None
            if os.path.exists(path+'/job.json'):
                with open(path+'/job.json') as f:
                    data = json.load(f)
                    if data: job_data = data

            if not job_data: return 'no job data'

            version = job_data['model_version']
            os.system("mv "+path+"/job.json "+path+"/"+str(version))
            os.system("mv "+path+"/object-detection.pbtxt "+path+"/"+str(version))

            model_file_path = path+"/"+str(version)+"/saved_model/saved_model.pb"
            if os.path.exists(model_file_path):
                os.system("mv "+model_file_path+" "+path+"/"+str(version))

            vars_path = path+"/"+str(version)+"/saved_model/variables"
            if os.path.exists(vars_path):
                os.system("mv "+vars_path+" "+path+"/"+str(version))

            os.system("rm -rf "+fn)

            j_upload = job_queue.enqueue(upload_model, str(path), str(fl.filename),
                            job_timeout=600,
                            result_ttl=3600,
                            retry=Retry(max=5, interval=60),
                        )

            if j_upload: insert_job(j_upload.id, 'Uploading models')
        except zipfile.BadZipfile:
            print('bad zipfile in ',fn)

def register_routes(api):
    api.add_resource(CategoryIndex, '/category_index/<string:model>/<string:version>')
    api.add_resource(DownloadModels, '/download_models')
    api.add_resource(DownloadPrograms, '/download_programs')
    api.add_resource(UploadModel, '/upload_model')
