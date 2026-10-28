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

processed = {}
ftp_directory = "/home/ftp"
POLL_INTERVAL = .5
IMAGE_EXTENSIONS = (".jpg", ".png", ".tif", ".bmp")


def reset_queue():
    """Clear the queue on startup.

    Destructive, and deliberately not run at import: importing this module used
    to drop the jobs collection as a side effect, which is why nothing could
    load it to test it.
    """
    job_queue.empty()
    job_collection.drop()


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
    if filename.endswith(IMAGE_EXTENSIONS):
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


def scan_once():
    """One pass over the watched directory."""
    global processed

    if not os.path.exists(ftp_directory):
        return

    if not len(os.listdir(ftp_directory)):
        processed = {}
        return

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


def main():
    reset_queue()
    while True:
        print('watching: '+ftp_directory)
        time.sleep(POLL_INTERVAL)
        scan_once()


if __name__ == '__main__':
    main()
