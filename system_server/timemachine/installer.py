import requests 
import getopt, sys
from datetime import datetime
import os
from redis import Redis
from rq import Queue, Worker, Connection
from rq.job import Job
from worker_scripts.job_manager import insert_job
import time
import settings

redis_con   = Redis('localhost', 6379, password=None)
job_queue   = Queue('default', connection=redis_con)

CLOUD_DOMAIN = settings.config['cloud_domain'] if 'cloud_domain' in settings.config else "https://clouddeploy.api.flexiblevision.com"

def cloud_install():
    #call to setup servers in the cloud
    os.system("chmod +x "+os.environ['HOME']+"/flex-run/system_server/timemachine/cloud.sh")
    os.system("sh "+os.environ['HOME']+"/flex-run/system_server/timemachine/cloud.sh ")

def local_zip_push_install(tm_type):
    time.sleep(5)
    #run local setup
    os.system("chmod +x "+os.environ['HOME']+"/flex-run/system_server/timemachine/local_zip_push.sh")
    os.system("sh "+os.environ['HOME']+"/flex-run/system_server/timemachine/local_zip_push.sh "+tm_type)

    verify_install = job_queue.enqueue(verify_local_install, 
                        job_timeout=600,
                        result_ttl=3600, 
                        retry=Retry(max=5, interval=60),
                    )


    insert_job(verify_install.id, 'verify timemachine install')

    return True

def verify_local_install():
    did_install = []
    #verify local eventor is running
    try:
        res = requests.get('http://172.17.0.1:1934/api/eventor/actions/server_status')
        did_install.append(res.status_code == 200)
    except Exception as error:
        print(error)
        did_install.append(False)

   #verify rtsp server is running
    try:
        res = requests.get('http://localhost:9997/v1/paths/list')
        did_install.append(res.status_code == 200)
    except Exception as error:
        print(error)
        did_install.append(False)

    return all(did_install)

def validate_account(service, access_token):
    is_valid = True #TESTING ONLY
    headers = {'Authorization': 'Bearer '+access_token}
    data    = {'service': service}
    try:
        res = requests.post(CLOUD_DOMAIN+'/api/capture/auth/validate_service', headers=headers, json=data)
        if res.status_code == 200:
            return res.json
    except Exception as erro:
        return is_valid

    return is_valid

def main():
    # list of command line arguments
    argumentList = sys.argv[1:]

    if '-t' not in argumentList:
        print('Type of timemachine \ne.g \"python3 installer.py -t local\"  -  types = [local, zip_push, cloud]')
        return 

    # Options
    options = "t:"
    
    # Long options
    long_options = ["Type"]

    try:
        # Parsing argument
        arguments, values = getopt.getopt(argumentList, options, long_options)    
        tm_type = 'local' #default    
        # checking each argument
        for currentArgument, currentValue in arguments:
            if currentArgument in ("-u", "--Type"):
                tm_type = currentValue

        if tm_type == 'local' or tm_type == 'zip_push':
            #run local setup
            local_zip_push_install(tm_type)

            # local_zip_push_install(tm_type)
        elif tm_type == 'cloud':
            #run cloud installer
            cloud_install()
            
    except getopt.error as err:
        # output error, and return with an error code
        print (str(err))


if __name__ == "__main__":
    main()

