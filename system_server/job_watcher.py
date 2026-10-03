import os
import time
from redis import Redis
from rq import Queue, Worker
from rq.job import Job
from pymongo import MongoClient
import subprocess
import uuid

client            = MongoClient("172.17.0.1")
job_collection    = client["fvonprem"]["jobs"]
failed_jobs       = client["fvonprem"]["failed_jobs"]

redis_con = Redis('localhost', 6379, password=None)
job_queue = Queue('default', connection=redis_con)

POLL_INTERVAL = .5


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


def reconcile_job(job):
    """Bring one tracked job's mongo record in line with its rq status."""
    j = job_queue.fetch_job(job['_id'])
    if not j:
        return

    # Read once: get_status() is a redis round trip per call.
    status = j.get_status()

    if status == 'finished':
        job_collection.delete_one({'_id': job['_id']})
        j.delete()
    elif status == 'failed':
        insert_failed_job(j)
        job_collection.delete_one({'_id': job['_id']})
    elif status != 'started':
        msg = 'job_'+j.id+'_'+status
        job_collection.update_one({'_id': job['_id']}, {'$set': {'type': msg}})


def reconcile_once():
    for job in job_collection.find():
        reconcile_job(job)


def main():
    while True:
        time.sleep(POLL_INTERVAL)
        reconcile_once()


if __name__ == '__main__':
    main()
