import os
import time
from redis import Redis
from rq import Queue, Worker, Connection
from rq.job import Job
from pymongo import MongoClient
import subprocess
import uuid

client            = MongoClient("172.17.0.1")
job_collection    = client["fvonprem"]["jobs"]

redis_con = Redis('localhost', 6379, password=None)
job_queue = Queue('default', connection=redis_con)

while True:
    time.sleep(.5)
    jobs = job_collection.find()
    for job in jobs:
        j = job_queue.fetch_job(job['_id'])
        if j and j.get_status() == 'finished':
            job_collection.delete_one({'_id': job['_id']})
        elif j.get_status() != 'started':
            msg = 'job_'+j.id+'_'+j.get_status()
            job_collection.update_one({'_id': job['_id']}, {'$set': {'type': msg}})
