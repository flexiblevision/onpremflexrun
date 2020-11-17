import os
import time
from redis import Redis
from rq import Queue, Worker, Connection
from rq.job import Job
from worker_scripts.process_ftp import process_img
from pymongo import MongoClient
import subprocess
import uuid

client            = MongoClient("172.17.0.1")
job_collection    = client["fvonprem"]["jobs"]

redis_con = Redis('localhost', 6379, password=None)
job_queue = Queue('default', connection=redis_con)

#clean the queue on init
job_queue.empty()
job_collection.drop()
processed = {}
directory = "/home/ftp"

def insert_job_ref(job_id, filename):
    tn = time.time_ns() // 1000000 

    job_collection.insert_one({
        '_id': job_id,
        'type': 'ftp_job_'+filename,
        'start_time': str(tn),
        'status': 'running'
    })


while True:
    print('watching: '+directory)
    time.sleep(.5)
    if os.path.exists(directory):
        if len(os.listdir(directory)):
            for filename in os.listdir(directory):
                if filename not in processed and filename.endswith(".jpg") or filename.endswith(".png"):
                    extension = filename[-4:]
                    if not filename.startswith( 'ftp_' ):
                        tn = time.time_ns() // 1000000 
                        rename = 'ftp_'+str(tn)+extension #rename file to prevent parsing errors
                        subprocess.call(['mv', directory+'/'+filename, directory+'/'+rename])
                        filename = rename
                        
                    print('processing: '+filename)
                    processed[filename] = "processing"
                    job_id     = str(uuid.uuid4())
                    os.system('sudo docker cp '+directory+'/'+filename+' capdev:/fvbackend/'+filename)
                    #add file to redis queue
                    insert_job_ref(job_id, filename)
                    job_queue.enqueue(process_img, filename, job_id, job_timeout=99999999, result_ttl=-1)
                else:
                    #remove files that are not jpg/png
                    os.system('rm '+directory+'/'+filename)
                    continue
        else:
            processed = {}


