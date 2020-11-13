import os
import time
from redis import Redis
from rq import Queue, Worker, Connection
from rq.job import Job
from worker_scripts.process_ftp import process_img

redis_con = Redis('localhost', 6379, password=None)
job_queue = Queue('default', connection=redis_con)

processed = {}
directory = "/home/ftp"

while True:
    print('watching: '+directory)
    time.sleep(.5)
    if os.path.exists(directory):
        if len(os.listdir(directory)):
            for filename in os.listdir(directory):
                if filename not in processed and filename.endswith(".jpg") or filename.endswith(".png"):
                    print('processing: '+filename)
                    processed[filename] = "processing"
                    #add file to redis queue
                    job_queue.enqueue(process_img, filename, job_timeout=99999999, result_ttl=-1)
                else:
                    continue
        else:
            processed = {}
