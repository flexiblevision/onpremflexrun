import requests 
import getopt, sys
from datetime import datetime
import os


def cloud_install():
    #call to setup servers in the cloud
    os.system("chmod +x "+os.environ['HOME']+"/flex-run/system_server/timemachine/cloud.sh")
    os.system("sh "+os.environ['HOME']+"/flex-run/system_server/timemachine/cloud.sh ")

def local_zip_push_install(tm_type):
    #run local setup
    os.system("chmod +x "+os.environ['HOME']+"/flex-run/system_server/timemachine/local_zip_push.sh")
    os.system("sh "+os.environ['HOME']+"/flex-run/system_server/timemachine/local_zip_push.sh "+tm_type)
    return verify_local_install()

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
        elif tm_type == 'cloud':
            #run cloud installer
            cloud_install()
            
    except getopt.error as err:
        # output error, and return with an error code
        print (str(err))


if __name__ == "__main__":
    main()

