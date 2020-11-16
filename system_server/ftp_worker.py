import os
import time
from redis import Redis
from rq import Queue, Worker, Connection
from rq.job import Job
from worker_scripts.process_ftp import process_img
import subprocess

redis_con = Redis('localhost', 6379, password=None)
job_queue = Queue('default', connection=redis_con)

#clean the queue on init
job_queue.empty()
processed = {}
directory = "/home/ftp"

while True:
    print('watching: '+directory)
    time.sleep(.5)
    if os.path.exists(directory):
        if len(os.listdir(directory)):
            for filename in os.listdir(directory):
                if filename not in processed and filename.endswith(".jpg") or filename.endswith(".png"):
                    tn = time.time_ns() // 1000000 
                    rename = 'ftp_'+str(tn)+'.jpg' #rename file to prevent parsing errors
                    subprocess.call(['mv', directory+'/'+filename, directory+'/'+rename])
                    filename = rename
                    print('processing: '+filename)
                    processed[filename] = "processing"
                    os.system('sudo docker cp '+directory+'/'+filename+' capdev:/fvbackend/'+filename)
                    #add file to redis queue
                    job_queue.enqueue(process_img, filename, job_timeout=99999999, result_ttl=-1)
                else:
                    continue
        else:
            processed = {}
