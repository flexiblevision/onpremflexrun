import os
import time
from pymongo import MongoClient
import subprocess
import requests
import uuid
import random
import string
from datetime import datetime as dt

client            = MongoClient("172.17.0.1")
job_collection    = client["fvonprem"]["jobs"]
models_collection = client["fvonprem"]["models"]

def insert_job(job_id):
    datetime_now = dt.now()
    job_collection.update_one({'job_id': job_id},
        {'$set': 
            {
                '_id': job_id,
                'job_id': job_id,
                'status': 'running',
                'start_time': int(datetime_now.timestamp()*1000),
                'job_type': 'loading_prediction',
                'type': 'loading prediction server...'
            }
        },
        True
    )


def main():
    model = models_collection.find_one({})
    if model:
        model_name = model['type']
        model_version = model['versions'][0]

        url = "http://172.17.0.1:8501/v1/models/"+model_name+"/metadata"
        status = False
        job_id = ''.join(random.choices(string.ascii_uppercase +
                             string.digits, k = 7)) 

        while True:
            try:
                res = requests.get(url)
                print(res.status_code == 200)
                job_collection.delete_many({'job_type': 'loading_prediction'})
            except:
                print('FAILED')
                insert_job(job_id)

            time.sleep(10)



if __name__ == "__main__":
    main()
