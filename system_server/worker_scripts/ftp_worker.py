#check if ftp enabled 

# if enabled - check if /root/ftp folder exists

# if folder exists - watch folder contents for new file 

# if new file - check to see if user has enabled process settings

# if no process settings - delete file 


import os
import time
from redis import Redis
from rq import Queue, Worker, Connection
from rq.job import Job
import process_ftp

redis_con = Redis('localhost', 6379, password=None)
job_queue = Queue('default', connection=redis_con)

processed = {}
directory = os.environ['HOME']+"/ftp"

while True:
    time.sleep(.5)
    if os.path.exists(directory):
        if len(os.listdir(directory)):
            for filename in os.listdir(directory):
                if filename not in processed and filename.endswith(".jpg") or filename.endswith(".png"):
                    processed[filename] = "processing"
                    #add file to redis queue
                    job_queue.enqueue(process_ftp.process_img, filename, job_timeout=99999999, result_ttl=-1)
                else:
                    continue
        else:
            processed = {}
