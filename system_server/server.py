import os
import sys
settings_path = os.environ['HOME']+'/flex-run'
sys.path.append(settings_path)

from flask import make_response, request, current_app
from functools import update_wrapper

from flask import Flask, render_template
from flask_restful import Resource, Api
import json
from json import dumps
import subprocess
import auth
import requests
import re
import uuid

from flask_cors import CORS
from flask import jsonify
from pathlib import Path

import zipfile, io
import base64
import io
import time
from collections import defaultdict
from io import StringIO
from io import BytesIO
from version_check import *
from os.path import exists

from worker_scripts.retrieve_models import retrieve_models
from worker_scripts.retrieve_programs import retrieve_programs
from worker_scripts.retrieve_masks import retrieve_masks
from worker_scripts.model_upload_worker import upload_model
from worker_scripts.job_manager import insert_job, push_analytics_to_cloud, get_next_analytics_batch
from helpers.config_helper import write_settings_to_config, set_dhcp
from timemachine.installer import *
from timemachine.cleanup import cleanup_timemachine_records
from timemachine.zip_push import push_event_records, get_unprocessed_events
from setup.management import generate_environment_config, update_config
import platform 
import datetime
import settings

if platform.processor() != 'aarch64':
    from gpio.gpio_helper import toggle_pin, set_pin_state

if 'use_aws' in settings.config and settings.config['use_aws'] and settings.FireOperator == None:
    try:
        from aws.FireOperator import FireOperator
        FireOperator  = FireOperator()
        settings.FireOperator = FireOperator
    except Exception as error:
        print(error, ' << initializing fire operator')

from redis import Redis
from rq import Queue, Retry, Worker, Connection
from rq.job import Job
import socket
import tempfile

from pymongo import MongoClient, ASCENDING
from bson import json_util, ObjectId

client        = MongoClient("172.17.0.1")
interfaces_db = client["fvonprem"]["interfaces"]
tm_records_db = client["fvonprem"]["event_records"]
utils_db      = client["fvonprem"]["utils"]

app = Flask(__name__)
api = Api(app)

CORS(app)
NUM_CLASSES = 99
redis_con   = Redis('localhost', 6379, password=None)
job_queue   = Queue('default', connection=redis_con)
CONTAINERS  = {
    'backend':'capdev', 
    'frontend':'captureui', 
    'prediction':'localprediction',
    'predict lite': 'predictlite',
    'nodecreator': 'nodecreator',
    'vision': 'vision',
    'database': 'mongo',
    'visiontools': 'visiontools'
}

CLOUD_DOMAIN = settings.config['cloud_domain'] if 'cloud_domain' in settings.config else "https://clouddeploy.api.flexiblevision.com"

daemon_services_list = {
    "FlexRun Server": "server.py",
    "TCP Server": "tcp/tcp_server.py",
    "GPIO Server": "gpio/gpio_controller.py",
    "Sync Worker": "worker_scripts/sync_worker.py",
    "Worker Server": "worker.py",
    "Inference Server Watcher": "worker_scripts/ping_prediction_server.py",
    "Job Watcher": "job_watcher.py"
}

def base_path():
    #mounted memory to ssd
    xavier_ssd = '/xavier_ssd/'
    return xavier_ssd if os.path.exists(xavier_ssd) else '/'

BASE_PATH_TO_MODELS = base_path()+'models/'
BASE_PATH_TO_LITE_MODELS = base_path()+'lite_models/'

for p in [BASE_PATH_TO_MODELS, BASE_PATH_TO_LITE_MODELS]:
    if not os.path.exists(p): os.makedirs(p)

def is_valid_ip(ip):
    if not ip: return False
    m = re.match(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$", ip)
    return bool(m) and all(map(lambda n: 0 <= int(n) <= 255, m.groups()))

def get_static_ip_ref():
    static_ip  = settings.config['static_ip'] if 'static_ip' in settings.config else '192.168.10.35'
    # path_ref   = os.path.expanduser('~/flex-run/setup_constants/static_ip.txt')
    # try:
    #     with open(path_ref, 'r') as file:
    #         static_ip = file.read().replace('\n', '')
    # except: return static_ip
    return static_ip

def get_interface_name_ref():
    eth_ports = get_eth_port_names()
    if len(eth_ports) <= 1:
        interface_name  = 'enp0s31f6'
    else:
        interface_name = get_eth_port_names()[-1]

    return interface_name

def set_static_ips(network = None):
    #static_ip      = get_static_ip_ref()
    ips            = []
    interface_name = get_interface_name_ref()
    if is_valid_ip(network):
        ips.append(network+'/24')

    #ips.append(static_ip+'/24') #append static ip
    ip_string = '['
    for ip in ips: ip_string += (ip) 
    ip_string = ip_string + ']'

    with open ('/etc/netplan/fv-net-init.yaml', 'w') as f:
        f.write('network:\n')
        f.write('  version: 2\n')
        f.write('  ethernets:')
        f.write('    '+interface_name+':\n')
        f.write('      dhcp4: false\n')
        f.write('      mtu: 9000\n')
        f.write('      addresses: '+ip_string)
    
    os.system("sudo netplan apply")

def set_ips(settings):
    store_netplan_settings(settings)
    build_set_netplan()
    set_dhcp()

def build_set_netplan():
    interfaces = []
    res = interfaces_db.find()
    interfaces = json.loads(json_util.dumps(res))

    if os.path.exists('/etc/netplan/'):
        with open ('/etc/netplan/fv-net-init.yaml', 'w') as f:
            f.write('network:\n')
            f.write('  version: 2\n')
            f.write('  ethernets:')
            for i in interfaces:
                f.write('\n    '+i['iname']+':\n')
                f.write('      dhcp4: '+str(i['dhcp'])+'\n')
                f.write('      mtu: 9000\n')
                f.write('      addresses: '+i['ip_string'])
        
        os.system("sudo netplan apply")
    else:
        print('netplan path does not exist')

def store_netplan_settings(i_config):
    try:
        #store in interfaces db
        iname, ip, dhcp = i_config['lanPort'], i_config['ip'], i_config['dhcp']

        ips = []
        if is_valid_ip(ip):
            ips.append(ip+'/24')
        else:
            raise Exception('Failed')

        ip_string = '['
        for ip in ips: ip_string += (ip) 
        ip_string = ip_string + ']'

        i_entry = {
            '_id': iname,
            'ip': ip,
            'ip_string': ip_string,
            'updated': str(datetime.datetime.now()),
            'dhcp': dhcp,
            'iname': iname
        }

        interfaces_db.update_one(
                {"iname": iname},
                {"$set": i_entry}, True)
    except Exception as error:
        print(error)

def get_mac_id():
    ifconfig  = subprocess.Popen(['ifconfig'], stdout=subprocess.PIPE).communicate()[0].decode('utf-8')
    if 'wl' in ifconfig:
        interface = 'wl' + ifconfig.split('wl')[1].split(':')[0]
    elif 'enp' in ifconfig:
        interface = 'enp' + ifconfig.split('enp')[1].split(':')[0]
    else:
        return None

    cmd = subprocess.Popen(['cat', '/sys/class/net/'+interface+'/address'], stdout=subprocess.PIPE)
    cmd_out, cmd_err = cmd.communicate()
    return  cmd_out.strip().decode("utf-8")

def system_info():
    out = subprocess.Popen(['lshw', '-short'], stdout=subprocess.PIPE)
    cmd = subprocess.Popen(['grep', 'system'], stdin=out.stdout, stdout=subprocess.PIPE)
    cmd_out, cmd_err = cmd.communicate()
    system = cmd_out.strip().decode("utf-8")
    system = " ".join(system.split())
    return system

def system_arch():
    cmd = subprocess.Popen(['arch'], stdout=subprocess.PIPE)
    cmd_out, cmd_err = cmd.communicate()
    return  cmd_out.strip().decode("utf-8")

def restart_network_manager():
    os.system("service network-manager restart")

def get_eth_port_names():
    eth_names = []
    names = os.popen('basename -a /sys/class/net/*').read()
    lan_port_num = 1
    for idx, n in enumerate(names.split('\n')):
        if re.match(r"^eth|^en", n):
            eth_names.append(n)
    
    eth_names.sort()
    return eth_names

def list_usb_paths():
    valid_formats = ['vfat', 'exfat']
    mount_paths   = []
    for format_type in valid_formats:
        usb_list = subprocess.Popen(['sudo', 'blkid', '-t', 'TYPE='+format_type, '-o', 'device'], stdout=subprocess.PIPE)
        usb = usb_list.communicate()[0].decode('utf-8').splitlines()
        if len(usb) > 0:
            usb = usb[-1].split('/')[-1]
            mount_paths.append(usb)

    return mount_paths

def get_lan_ips():
    #lanIps   = {'LAN1': 'not assigned', 'LAN2': 'not assigned'}
    lans = []
    eth_names = get_eth_port_names()
    for idx, eth in enumerate(eth_names):
        lanIps = {}
        idx += 1
        lan_port = 'LAN'+str(idx)
        lanIps['ip']   = 'not assigned'
        lanIps['port'] = eth
        lanIps['name'] = lan_port
        lanIps['dhcp'] = False
        interface = subprocess.Popen(['ifconfig', eth], stdout=subprocess.PIPE).communicate()[0].decode('utf-8')
        i_entry   = interfaces_db.find_one({'iname': eth})
        if i_entry: lanIps['dhcp'] = i_entry['dhcp']

        ip6 = None
        ip  = 'LAN IP not assigned'
        if 'inet6' in interface:
            ip6 = interface.split('inet6')[1].split(' ')[1]
        if 'inet' in interface:
            ip = interface.split('inet')[1].split(' ')[1]
        else:
            if idx > 2:
                #assign port a default IP
                if not i_entry:
                    ip   = '192.168.{}.10'.format(5+idx)
                    data = {'ip': ip, 'lanPort': eth, 'dhcp': False}
                    if data['ip'] != '' and is_valid_ip(data['ip']):
                        set_ips(data)
                        os.system('sudo ifconfig ' + eth + ' '  + data['ip'] + ' netmask 255.255.255.0')

        if ip6 and ip6 == ip: ip = 'LAN IP not assigned'
        lanIps['ip'] = ip
        lans.append(lanIps)

    return lans

class MacId(Resource):
    def get(self):
        return get_mac_id()

class Shutdown(Resource):
    @auth.requires_auth
    def get(self):
        print('shutting down system')
        os.system("poweroff")

class Restart(Resource):
    @auth.requires_auth
    def get(self):
        print('restarting system')
        os.system("reboot")

class RestartBackend(Resource):
    @auth.requires_auth
    def get(self):
        print('restarting capdev and vision...')
        os.system("docker restart capdev")
        #call to vision server to release all cameras
        try:
            host    = 'http://172.17.0.1'
            port    = '5555'
            path    = '/api/vision/releaseAll'
            url     = host+':'+port+path
            resp    = requests.get(url)
        except Exception as e:
            print(e)
        os.system("docker restart vision")

class ListServices(Resource):
    def get(self):
        f_services = []
        scripts_base_path = os.environ['HOME']+"/flex-run/system_server/"
        for key in daemon_services_list:
            service_path = scripts_base_path + daemon_services_list[key]
            is_running   = subprocess.getoutput("forever list | grep {} | wc -l | sed -e 's/1/Running/' | sed -e 's/0/Not Running/'".format(service_path))
            color = 'green' if is_running == "Running" else 'red'
            txt = key + " - " + is_running
            f_services.append({'txt': txt, 'color': color})

        c_services = []
        for f_name in CONTAINERS:
            container_name = CONTAINERS[f_name]
            inspect = subprocess.Popen(['docker', 'inspect', '-f', "{{.State.Running}}", container_name], stdout=subprocess.PIPE)
            is_running = inspect.communicate()[0].decode('utf-8').strip()
            color = 'green' if is_running=='true' else 'red'
            r_txt = 'Running' if is_running=='true' else 'Not Running'
            txt = f_name + " - " + r_txt
            c_services.append({'txt': txt, 'color': color})

        resp = make_response(render_template('services_doc.html', daemon_services=f_services, container_services=c_services))
        resp.headers['Content-type'] = 'text/html; charset=utf-8'
        return resp

class TogglePin(Resource):
    #@auth.requires_auth
    def put(self):
        j = request.json
        if 'pin_num' in j:
            try:
                toggle_pin(j['pin_num'])
                return True
            except:
                return False
        else:
            return False

class SetPin(Resource):
    def put(self):
        j = request.json
        if 'pin_num' in j and 'state' in j:
            return set_pin_state(j['pin_num'], j['state'])
        else:
            return -1

class Upgrade(Resource):
    @auth.requires_auth
    def get(self):
        cap_uptd         = is_container_uptodate('backend')[1]
        capui_uptd       = is_container_uptodate('frontend')[1]
        predict_uptd     = is_container_uptodate('prediction')[1]
        predictlite_uptd = is_container_uptodate('predictlite')[1]
        vision_uptd      = is_container_uptodate('vision')[1]
        creator_uptd     = is_container_uptodate('nodecreator')[1]
        visiontools_uptd = is_container_uptodate('visiontools')[1]

        try:
            host    = 'http://172.17.0.1'
            port    = '5555'
            path    = '/api/vision/releaseAll'
            url     = host+':'+port+path
            resp    = requests.get(url)
        except Exception as e:
            print(e)

        #upgrade flex run 
        generate_environment_config()
        os.system("chmod +x "+os.environ['HOME']+"/flex-run/upgrades/upgrade_flex_run.sh")
        os.system("sh "+os.environ['HOME']+"/flex-run/upgrades/upgrade_flex_run.sh")

        #upgrade containers 
        os.system("chmod +x "+os.environ['HOME']+"/flex-run/system_server/upgrade_system.sh")
        os.system("sh "+os.environ['HOME']+"/flex-run/system_server/upgrade_system.sh "+cap_uptd+" "+capui_uptd+" "+predict_uptd+" "+predictlite_uptd+" "+vision_uptd+" "+creator_uptd+" "+visiontools_uptd)

class UpgradeFlexRun(Resource):
    @auth.requires_auth
    def get(self):
        os.system("chmod +x "+os.environ['HOME']+"/flex-run/upgrades/upgrade_flex_run.sh")
        os.system("sh "+os.environ['HOME']+"/flex-run/upgrades/upgrade_flex_run.sh")

class EnableTimemachine(Resource):
    @auth.requires_auth
    def post(self):
        j = request.json
        access_token = request.headers.get('Access-Token')
        if not access_token: return 'Access-Token header is required', 403

        tm_types    = ['local', 'cloud', 'zip_push']
        did_install = False
        authorized  = validate_account('time_machine', access_token)
        if not authorized: return 'Account is not authorized to use the Time Machine feature.', 403
        if 'type' in j:
            if j['type'] in tm_types:
                if j['type'] == 'local' or j['type'] == 'zip_push':
                    install_job = job_queue.enqueue(local_zip_push_install, j['type'], job_timeout=99999999, result_ttl=-1)
                    job = insert_job(install_job.id, 'installing time machine locally')
                    did_install = True
                else:
                    did_install =  cloud_install()
            else:
                return 'type must be one of the following: [local, cloud, zip_push]', 500
        else:
            return 'missing type key. Type key must be passed',500

        if did_install:
            return True, 200
        else:
            return False, 500

class DisableTimemachine(Resource):
    @auth.requires_auth
    def delete(self):
        j = request.json
        tm_types = ['local', 'cloud', 'zip_push']
        if 'type' in j:
            if j['type'] == 'local' or j['type'] == 'zip_push':
                os.system('sh '+os.environ['HOME']+'/flex-run/system_server/timemachine/uninstaller.sh')
            else:
                print('uninstall cloud timemachine')
            return True, 200
        else:
            return 'missing type key. Type key must be passed',500

class AuthToken(Resource):
    @auth.requires_auth
    def get(self):
        cmd = subprocess.Popen(['cat', os.environ['HOME']+'/flex-run/system_server/creds.txt'], stdout=subprocess.PIPE)
        cmd_out, cmd_err = cmd.communicate()
        cleanStr = cmd_out.strip().decode("utf-8")
        if cleanStr: return cleanStr

    @auth.requires_auth
    def post(self):
        j = request.json
        if j:
            if 'obj' in j and 'server_ip' in j['obj']:
                settings.config['cloud_domain'] = 'http://{}'.format(j['obj']['server_ip'])
                write_settings_to_config()
            os.system('echo '+j['refresh_token']+' > '+os.environ['HOME']+'/flex-run/system_server/creds.txt')
            return True
        return False

class Networks(Resource):
    def get(self):
        try:
            networks = subprocess.check_output(['nmcli', '-f', 'SSID', 'dev', 'wifi'])
        except:
            print('ERROR - NETWORK MANAGER NOT FOUND')
            restart_network_manager()
            return 
        nets = {}
        network_list = []
        for i in networks.splitlines():
            i = i.decode('utf-8').strip()
            if i not in network_list and i != '' and i != 'SSID':
                network_list.append(i)

        for i,line in enumerate(network_list):
            nets[i] = line

        ifconfig = subprocess.Popen(['ifconfig'], stdout=subprocess.PIPE).communicate()[0].decode('utf-8')
        
        interface = 'wl' + ifconfig.split('wl')[1].split(':')[0]

        wlp = subprocess.Popen(['ifconfig', interface], stdout=subprocess.PIPE).communicate()[0].decode('utf-8')
        
        if 'inet' in wlp:
            nets['ip'] = wlp.split('inet')[1].split(' ')[1]
        else:
            nets['ip'] = 'Wi-Fi not connected'

        return nets

    def post(self):
        j = request.json
        return os.system("nmcli dev wifi connect "+j['netName']+" password "+j['netPassword'])

class CategoryIndex(Resource):
    def get(self, model, version):
        # category index will now be created from the job.json file
        # read in json file and parse the labelmap_dict to create the category_index

        model_path = None
        path_to_model_labels = BASE_PATH_TO_MODELS + model + '/' + version + '/job.json'
        path_to_lite_models  = BASE_PATH_TO_LITE_MODELS + model + '/' + version + '/job.json'

        model_paths = [path_to_model_labels, path_to_lite_models]
        for path in model_paths:
            if exists(path):
                model_path = path
                break
        
        if not model_path: return {}

        labels = None
        with open(model_path) as data:
            labels = json.load(data)['labelmap_dict']

        category_index = {}
        for key in labels.keys():
            _id = labels[key]
            category_index[_id] = {"id": _id, "name": key}

        return category_index

class DownloadModels(Resource):
    @auth.requires_auth
    def post(self):
        data           = request.json
        access_token   = request.headers.get('Access-Token')
        
        j_models = job_queue.enqueue(retrieve_models, data, access_token, job_timeout=99999999, result_ttl=-1)
        j_masks  = job_queue.enqueue(retrieve_masks, data, access_token, job_timeout=99999999, result_ttl=-1)
        j_progs  = job_queue.enqueue(retrieve_programs, data, access_token, job_timeout=9999999, result_ttl=-1) 

        if j_models: insert_job(j_models.id, 'Downloading models')
        if j_masks: insert_job(j_masks.id, 'Downloading masks')
        if j_progs: insert_job(j_progs.id, 'Downloading programs')
        return True

class DownloadPrograms(Resource):
    @auth.requires_auth
    def post(self):
        data           = request.json
        access_token   = request.headers.get('Access-Token')
        
        j_progs  = job_queue.enqueue(retrieve_programs, data, access_token, job_timeout=9999999, result_ttl=-1) 
        if j_progs: insert_job(j_progs.id, 'Downloading programs')

        return True    

class SystemVersions(Resource):
    def get(self):
        backend_version    = get_current_container_version('capdev')
        frontend_version   = get_current_container_version('captureui')
        prediction_version = get_current_container_version('localprediction')
        predictlite_version = get_current_container_version('predictlite')
        vision_version      = get_current_container_version('vision')
        creator_version     = get_current_container_version('nodecreator')
        visiontools_version = get_current_container_version('visiontools')

        
        return {'backend_version': backend_version,
                'frontend_version': frontend_version,
                'prediction_version': prediction_version,
                'predictlite_version': predictlite_version,
                'vision_version': vision_version,
                'creator_version': creator_version,
                'visiontools_version': vision_version
                }

class SystemIsUptodate(Resource):
    def get(self):
        return all([
            is_container_uptodate('backend')[0], 
            is_container_uptodate('frontend')[0], 
            is_container_uptodate('prediction')[0], 
            is_container_uptodate('predictlite')[0],
            is_container_uptodate('vision')[0],
            is_container_uptodate('nodecreator')[0],
            is_container_uptodate('visiontools')[0]
        ])

class DeviceInfo(Resource):
    def get(self):
        info = {}
        domain = request.headers.get('Host').split(':')[0]
        ifconfig  = subprocess.Popen(['ifconfig'], stdout=subprocess.PIPE).communicate()[0].decode('utf-8')
        interface = 'wl' + ifconfig.split('wl')[1].split(':')[0]
        wlp       = subprocess.Popen(['ifconfig', interface], stdout=subprocess.PIPE).communicate()[0].decode('utf-8')
        
        if 'inet' in wlp:
            info['last_known_ip'] = wlp.split('inet')[1].split(' ')[1]
        else:
            info['last_known_ip'] = domain
        
        lan_ips = get_lan_ips()
        for lan in lan_ips:
            ip = lan['ip']
            if ip != 'not assigned' and ip != 'LAN IP not assigned':
                info['last_known_ip'] = '{};{}'.format(ip, info['last_known_ip'])

        info['system']        = system_info()
        info['arch']          = system_arch()
        info['mac_id']        = get_mac_id()
        info['hotspot']       = settings.config['ssid'] if 'ssid' in settings.config else 'not configured'
        info['last_active']   = str(datetime.datetime.now())

        return info

class SaveImage(Resource):
    #@auth.requires_auth
    def post(self):
        data = request.json
        path = os.environ['HOME']+'/'+'stored_images'
        usb  = list_usb_paths()[-1]

        cmd_output = subprocess.Popen(['sudo', 'lsblk', '-o', 'MOUNTPOINT', '-nr', '/dev/'+usb], stdout=subprocess.PIPE)
        usb_mountpoint = cmd_output.communicate()[0].decode('utf-8')

        if '/boot/efi' in usb_mountpoint or usb_mountpoint == '':
            print('CANNOT EXPORT TO BOOT MOUNTPOINT')
            return False

        if 'img' in data:
            img_path   = path+'/flexible_vision/snapshots'
            todayDate  = time.strftime("%d-%m-%y")
            img_path   = img_path +'/' + todayDate
            decode_img = base64.b64decode(data['img'])

            if not os.path.exists(img_path):
                os.makedirs(img_path)

            img_path = img_path + '/' +str(int(datetime.datetime.now().timestamp()*1000))+'.jpg'
            with open(img_path, 'wb') as fh:
                fh.write(decode_img)

            if usb[0] == 's':
                os.system('sudo mount /dev/' + usb + ' ' + path)
                with open(img_path, 'wb') as fh:
                    fh.write(decode_img)
                os.system('sudo umount /dev/'+usb+' '+path)
                print('----- unmounted usb drive -----')

        return 'Image Saved', 200

class ExportImage(Resource):    
    #@auth.requires_auth
    def post(self):
        data = request.json
        path = os.environ['HOME']+'/usb_images'
        if not os.path.exists(path):
            os.system('mkdir '+path)

        usbs = list_usb_paths()
        last_connected_usb_path = usbs[-1]
        cmd_output = subprocess.Popen(['sudo', 'lsblk', '-o', 'MOUNTPOINT', '-nr', '/dev/'+last_connected_usb_path], stdout=subprocess.PIPE)
        usb_mountpoint = cmd_output.communicate()[0].decode('utf-8')

        if '/boot/efi' in usb_mountpoint or usb_mountpoint == '':
            print('CANNOT EXPORT TO BOOT MOUNTPOINT')
            return False

        usb = usbs[-1].split('/')[-1]
        if usb[0] == 's':
            os.system('sudo mount /dev/' + usb + ' ' + path)

            if 'img' and 'model' and 'version'  in data:
                did = ''
                base_path = path + '/flexible_vision/' + data['model'] + '/' + data['version']

                img_path  = base_path + '/images'
                todayDate = time.strftime("%d-%m-%y")
                img_path  = img_path +'/' + todayDate

                if not os.path.exists(img_path):
                    os.makedirs(img_path)

                if 'inference' in data:
                    inference = data['inference']
                    if 'did' in inference:
                        did = '_'+inference['did']

                timestamp  = str(datetime.datetime.now())
                img_path   = img_path + '/'+ timestamp.replace(' ', '_').replace('.', '_').replace(':', '-')+did+'.jpg'
                decode_img = base64.b64decode(data['img'])

                with open(img_path, 'wb') as fh:
                    print('writing to: ', img_path)
                    fh.write(decode_img)

                # --------------- export inference data ----------------------

                if 'inference' in data:
                    inference = data['inference']
                    #create inferences folder and add assets
                    inferences_path = base_path + '/inferences'
                    inferences_path = inferences_path +'/' + todayDate

                    if not os.path.exists(inferences_path):
                        os.makedirs(inferences_path)

                    file_path = inferences_path + '/'
                    if 'did' in inference:
                        did = '_'+inference['did']

                    file_path = file_path+timestamp.replace(' ', '_').replace('.', '_').replace(':', '-')+did+'.json'

                    with open(file_path, 'w') as fh:
                        json.dump(inference, fh)

                os.system('sudo umount /dev/'+usb+' '+path)
                print('----- unmounted usb drive -----')


class GetCameraUID(Resource):
    def get(self, idx):
        out = subprocess.Popen(['udevadm', 'info', '--query=all', '/dev/video'+idx], stdout=subprocess.PIPE)
        cmd = subprocess.Popen(['grep', 'VENDOR_ID\|MODEL_ID\|SERIAL_SHORT'], stdin=out.stdout, stdout=subprocess.PIPE)
        cmd_out, cmd_err = cmd.communicate()
        uid = cmd_out.strip().decode("utf-8")
        msv = uid.splitlines()
        uid = ''
        for i,did in enumerate(msv):
            uid += did.split('=')[-1]
            if i < len(msv)-1: uid += ':'
        # model : serial : vendor
        return uid

class UpdateIp(Resource):
    @auth.requires_auth
    def post(self):
        data = request.json
        ifconfig = subprocess.Popen(['ifconfig'], stdout=subprocess.PIPE).communicate()[0].decode('utf-8')

        eth_names = get_eth_port_names()
        if 'lanPort' in data and data['lanPort'] in eth_names:
            idx = eth_names.index(data['lanPort'])
            interface_name = eth_names[idx]
        else:
            return 'ethernet interface not found', 500
        
        if data['ip'] != '' and is_valid_ip(data['ip']):
            # set_static_ips(data['ip'])
            set_ips(data)
            os.system('sudo ifconfig ' + interface_name + ' '  + data['ip'] + ' netmask 255.255.255.0')
        else:
            return 'IP address invalid', 500

        interface = subprocess.Popen(['ifconfig', interface_name], stdout=subprocess.PIPE).communicate()[0].decode('utf-8')

        if 'inet' in interface:
            ip = interface.split('inet')[1].split(' ')[1]
        else:
            ip = 'LAN IP not assigned'

        return ip

class GetLanIps(Resource):
    def get(self):
        lanIps = get_lan_ips()
        return lanIps

class UploadModel(Resource):
    #@auth.requires_auth
    def post(self):
        fl = request.files['file']
        if not fl: return False 

        split_fname = fl.filename.split('#')
        model_name  = split_fname[0]

        #Temporarily write folder to root directory
        path = "/"+model_name
        if os.path.exists('/models'+path):
            print(path+' - already exists - REMOVING')
            os.system('rm -rf '+'/models'+path)

        os.system("mkdir "+path)
        fn = tempfile.gettempdir() + 'model.zip'
        fl.save(fn)

        try:
            print('EXTRACTING ZIP FILE')
            with zipfile.ZipFile(fn) as zf:
                zf.extractall(path)

            #read path/job.json to get version
            job_data = None
            if os.path.exists(path+'/job.json'):
                with open(path+'/job.json') as f:
                    data = json.load(f)
                    if data: job_data = data

            if not job_data: return 'no job data'

            version = job_data['model_version']
            os.system("mv "+path+"/job.json "+path+"/"+str(version))
            os.system("mv "+path+"/object-detection.pbtxt "+path+"/"+str(version))

            #move pb file to model version directory
            model_file_path = path+"/"+str(version)+"/saved_model/saved_model.pb"
            if os.path.exists(model_file_path):
                os.system("mv "+model_file_path+" "+path+"/"+str(version))

            #move variables folder to model version directory
            vars_path = path+"/"+str(version)+"/saved_model/variables"
            if os.path.exists(vars_path):
                os.system("mv "+vars_path+" "+path+"/"+str(version))

            os.system("rm -rf "+fn)

            j_upload = job_queue.enqueue(upload_model, str(path), str(fl.filename), job_timeout=99999999, result_ttl=-1)
            if j_upload: insert_job(j_upload.id, 'Uploading models')
        except zipfile.BadZipfile:
            print('bad zipfile in ',fn)

class AddFtpUser(Resource):
    @auth.requires_auth
    def post(self):
        data = request.json
        if 'username' and 'password' in data:
            home = os.environ['HOME']
            subprocess.call(["sh", home+"/flex-run/scripts/add_ftp_user.sh", data['username'], data['password']])
            return True
        return False

class DeleteFtpUser(Resource):
    @auth.requires_auth
    def delete(self):
        data = request.json

        if 'username' in data:
            os.system('sudo deluser -f '+data['username'])
            os.system('sudo rm -r /home/'+data['username'])            
            return True
        return False

class UpdateFtpPort(Resource):
    @auth.requires_auth
    def put(self):
        data = request.json

        if 'port' in data:
            home = os.environ['HOME']
            port = int(data['port'])

            if port > 0:
                subprocess.call(["sh", home+"/flex-run/scripts/update_ftp.sh", "listen_port", str(port)])
            return True
        return False

class EnableFtp(Resource):
    @auth.requires_auth
    def post(self):
        data = request.json
        if 'port' in data:
            home = os.environ['HOME']
            subprocess.call(["sh", home+"/flex-run/setup/ftp_server_setup.sh"])
            return True
        return False

class SyncAnalytics(Resource):
    @auth.requires_auth
    def get(self):
        access_token = request.headers.get('Access-Token')

        if access_token:
            push_analytics_to_cloud(CLOUD_DOMAIN, access_token)
            events = get_unprocessed_events()
            if events['count'] > 0:
                er_push = job_queue.enqueue(push_event_records, CLOUD_DOMAIN, access_token, 
                            events, job_timeout=80000, result_ttl=-1, retry=Retry(max=10, interval=60))                            
                if er_push: insert_job(er_push.id, 'Pushing '+str(events['count'])+' events to cloud')

class DeAuthorize(Resource):
    @auth.requires_auth
    def get(self):
        os.system("rm "+os.environ['HOME']+"/flex-run/system_server/creds.txt")

class CleanupTimemachine(Resource):
    @auth.requires_auth
    def delete(self):
        return cleanup_timemachine_records(), 200

class SyncFlow(Resource):
    @auth.requires_auth
    def get(self):
        access_token = request.headers.get('Access-Token')
        flow_path = "{}/flows.json".format(os.environ['HOME'])
        os.system("docker cp nodecreator:/root/.node-red/flows.json "+flow_path)
        dev_ref   = utils_db.find_one({'type':'device_id'})
        device_id =  None if not dev_ref else dev_ref['id']

        if not device_id: return 'device id not found', 404

        url = '{}/api/capture/devices/{}/flow'.format(CLOUD_DOMAIN, device_id)
        headers = {'Authorization' : 'Bearer {}'.format(access_token), 'Accept' : 'application/json', 'Content-Type' : 'application/json'}
        r = requests.post(url, data=open(flow_path, 'rb'), headers=headers)
        return r.text, r.status_code

class InspectionStatus(Resource):
    def post(self):
        data = request.json
        if settings.FireOperator:
            settings.FireOperator.update_status(data)
            return 'Updated', 200
        else:
            return 'Operator not running', 404

class AwsWarehouseZone(Resource):
    def get(self):
        results = {'warehouse': "", 'zone': ""}
        station = settings.config['fire_operator']['document']
        wz      = station.split('_')
        if len(wz) == 2:
            results['warehouse'] = wz[0]
            results['zone']      = wz[1]
        return results
    
    def put(self):
        data = request.json
        if 'warehouse' in data and 'zone' in data:
            doc_key = f"{data['warehouse']}_{data['zone']}"
            settings.config['fire_operator']['document'] = doc_key
            update_config(settings.config)

api.add_resource(AuthToken, '/auth_token')
api.add_resource(Networks, '/networks')
api.add_resource(MacId, '/mac_id')
api.add_resource(Shutdown, '/shutdown')
api.add_resource(Restart, '/restart')
api.add_resource(Upgrade, '/upgrade')
api.add_resource(CategoryIndex, '/category_index/<string:model>/<string:version>')
api.add_resource(DownloadModels, '/download_models')
api.add_resource(DownloadPrograms, '/download_programs')
api.add_resource(SystemVersions, '/system_versions')
api.add_resource(SystemIsUptodate, '/system_uptodate')
api.add_resource(DeviceInfo, '/device_info')
api.add_resource(SaveImage, '/save_img')
api.add_resource(ExportImage, '/export_img')
api.add_resource(UpdateIp, '/update_ip')
api.add_resource(GetLanIps, '/get_lan_ips')
api.add_resource(GetCameraUID, '/camera_uid/<string:idx>')
api.add_resource(TogglePin, '/toggle_pin')
api.add_resource(SetPin, '/set_pin')
api.add_resource(RestartBackend, '/refresh_backend')
api.add_resource(ListServices, '/list_services')
api.add_resource(UploadModel, '/upload_model')
api.add_resource(AddFtpUser, '/add_ftp_user')
api.add_resource(DeleteFtpUser, '/delete_ftp_user')
api.add_resource(UpdateFtpPort, '/update_ftp_port')
api.add_resource(EnableFtp, '/enable_ftp')
api.add_resource(SyncAnalytics, '/sync_analytics')
api.add_resource(UpgradeFlexRun, '/upgrade_flex_run')
api.add_resource(DeAuthorize, '/deauthorize')
api.add_resource(EnableTimemachine, '/enable_timemachine')
api.add_resource(DisableTimemachine, '/disable_timemachine')
api.add_resource(CleanupTimemachine, '/cleanup_timemachine')
api.add_resource(SyncFlow, '/sync_flow')

if 'use_aws' in settings.config and settings.config['use_aws']:
    api.add_resource(InspectionStatus, '/inspection_status')
    api.add_resource(AwsWarehouseZone, '/aws_warehouse_zone')


if __name__ == '__main__':
    app.run(host='0.0.0.0',port='5001')
