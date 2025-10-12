import os
import time
from redis import Redis
from rq import Queue, Worker
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
ftp_directory = "/home/ftp"

def insert_job_ref(job_id, filename):
    tn = time.time_ns() // 1000000 

    job_collection.insert_one({
        '_id': job_id,
        'type': 'ftp_job_'+filename,
        'start_time': str(tn),
        'status': 'running'
    })

def process_file(directory, filename):
    subprocess.call(['mv', directory+'/'+filename, directory+'/'+filename.lower()])
    if filename.endswith(".jpg") or filename.endswith(".png") or filename.endswith(".tif") or filename.endswith(".bmp"):
        if filename not in processed:
            extension = filename[-4:]
            if not filename.startswith( 'ftp_' ):
                tn = time.time_ns() // 1000000 
                rename = 'ftp_'+str(tn)+extension #rename file to prevent parsing errors
                subprocess.call(['mv', directory+'/'+filename, ftp_directory+'/'+rename])
                filename = rename
                
            print('processing: '+filename)
            processed[filename] = "processing"
            j = job_queue.enqueue(process_img, filename, job_timeout=99999999, result_ttl=-1)
            insert_job_ref(j.id, filename)
    else:
        #remove files that are not jpg/png
        os.system('rm '+directory+'/'+filename)
 

while True:
    print('watching: '+ftp_directory)
    time.sleep(.5)
    if os.path.exists(ftp_directory):
        if len(os.listdir(ftp_directory)):
            for filename in os.listdir(ftp_directory):
                file_path = ftp_directory+'/'+filename
                if os.path.isdir(file_path):
                    #search inside directory       
                    if len(os.listdir(file_path)) == 0: 
                        #os.system('rm -rf '+file_path)
                        continue
                    for subfilename in os.listdir(file_path):
                        sub_file_path = ftp_directory+'/'+filename+'/'+subfilename

                        if os.path.isdir(sub_file_path):
                            os.system('rm -rf '+sub_file_path)
                        else:
                            process_file(ftp_directory+'/'+filename, subfilename)
                else:
                    #process file
                    process_file(ftp_directory, filename)
        else:
            processed = {}


