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
failed_jobs       = client["fvonprem"]["failed_jobs"]

redis_con = Redis('localhost', 6379, password=None)
job_queue = Queue('default', connection=redis_con)


def insert_failed_job(j):
    failed_jobs.update_one({'job_id': j.id},
        {'$set': 
            {
                'job_id': j.id,
                'started_at': j.started_at,
                'ended_at': j.ended_at,
                'origin': j.origin
            }
        },
        True
    )

while True:
    time.sleep(.5)
    jobs = job_collection.find()
    for job in jobs:
        j = job_queue.fetch_job(job['_id'])
        if not j: continue
        if j and j.get_status() == 'finished':
            job_collection.delete_one({'_id': job['_id']})
        elif j.get_status() == 'failed':
            insert_failed_job(j)
            job_collection.delete_one({'_id': job['_id']})
        elif j.get_status() != 'started':
            msg = 'job_'+j.id+'_'+j.get_status()
            job_collection.update_one({'_id': job['_id']}, {'$set': {'type': msg}})
