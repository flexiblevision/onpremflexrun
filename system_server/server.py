from datetime import timedelta
from flask import make_response, request, current_app
from functools import update_wrapper

from flask import Flask
from flask_restful import Resource, Api
import json
from json import dumps
import subprocess
import os
import datetime
import auth
import requests
import re
import uuid

from flask_cors import CORS
from flask import jsonify
from pathlib import Path

import os
import sys
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

from gpio.gpio_helper import toggle_pin

from redis import Redis
from rq import Queue, Worker, Connection
from rq.job import Job
import socket
import tempfile

app = Flask(__name__)
api = Api(app)

CORS(app)
NUM_CLASSES = 99
redis_con   = Redis('localhost', 6379, password=None)
job_queue   = Queue('default', connection=redis_con)
CONTAINERS  = {'backend':'capdev', 'frontend':'captureui', 'prediction':'localprediction'}

CLOUD_DOMAIN = "https://clouddeploy.api.flexiblevision.com"
cloud_path   = os.path.expanduser('~/flex-run/setup_constants/cloud_domain.txt')
with open(cloud_path, 'r') as file: 
    CLOUD_DOMAIN = file.read().replace('\n', '')



def base_path():
    #mounted memory to ssd
    xavier_ssd = '/xavier_ssd/'
    return xavier_ssd if os.path.exists(xavier_ssd) else '/'

BASE_PATH_TO_MODELS = base_path()+'models/'
BASE_PATH_TO_LITE_MODELS = base_path()+'lite_models/'


def is_valid_ip(ip):
    if not ip: return False
    m = re.match(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$", ip)
    return bool(m) and all(map(lambda n: 0 <= int(n) <= 255, m.groups()))

def get_static_ip_ref():
    static_ip  = '192.168.0.10'
    path_ref   = os.path.expanduser('~/flex-run/setup_constants/static_ip.txt')
    try:
        with open(path_ref, 'r') as file:
            static_ip = file.read().replace('\n', '')
    except: return static_ip
    return static_ip

def get_interface_name_ref():
    interface_name  = 'enp0s31f6'
    path_ref        = os.path.expanduser('~/flex-run/setup_constants/interface_name.txt')
    try:
        with open(path_ref, 'r') as file:
            interface_name = file.read().replace('\n', '')
    except: return interface_name
    return interface_name

def set_static_ips(network = None):
    static_ip      = get_static_ip_ref()
    ips            = []
    interface_name = get_interface_name_ref()
    if is_valid_ip(network):
        ips.append(network+'/24')

    ips.append(static_ip+'/24') #append static ip
    ip_string = '['
    for ip in ips: ip_string += (ip+', ') 
    ip_string = ip_string + ']'

    with open ('/etc/netplan/fv-net-init.yaml', 'w') as f:
        f.write('network:\n')
        f.write('  version: 2\n')
        f.write('  ethernets:\n')
        f.write('    '+interface_name+':\n')
        f.write('      dhcp4: false\n')
        f.write('      addresses: '+ip_string)

def get_mac_id():
    ifconfig  = subprocess.Popen(['ifconfig'], stdout=subprocess.PIPE).communicate()[0].decode('utf-8')
    if 'wlp' in ifconfig:
        interface = 'wlp' + ifconfig.split('wlp')[1].split(':')[0]
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
        if 'en' in n:
            eth_names.append(n)
            
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
        print('restarting capdev...')
        os.system("docker restart capdev")

class TogglePin(Resource):
    #@auth.requires_auth
    def put(self):
        j = request.json
        if 'pin_num' in j:
            toggle_pin(j['pin_num'])
            return True
        else:
            return False

class Upgrade(Resource):
    @auth.requires_auth
    def get(self):
        cap_uptd     = is_container_uptodate('backend')[1]
        capui_uptd   = is_container_uptodate('frontend')[1]
        predict_uptd = is_container_uptodate('prediction')[1]
        predictlite_uptd = is_container_uptodate('predictlite')[1]

        os.system("chmod +x "+os.environ['HOME']+"/flex-run/system_server/upgrade_system.sh")
        os.system("sh "+os.environ['HOME']+"/flex-run/system_server/upgrade_system.sh "+cap_uptd+" "+capui_uptd+" "+predict_uptd+" "+predictlite_uptd)

class UpgradeFlexRun(Resource):
    @auth.requires_auth
    def get(self):
        os.system("chmod +x "+os.environ['HOME']+"/flex-run/upgrades/upgrade_flex_run.sh")
        os.system("sh "+os.environ['HOME']+"/flex-run/upgrades/upgrade_flex_run.sh")

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
        
        interface = 'wlp' + ifconfig.split('wlp')[1].split(':')[0]

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

class SystemVersions(Resource):
    def get(self):
        backend_version    = get_current_container_version('capdev')
        frontend_version   = get_current_container_version('captureui')
        prediction_version = get_current_container_version('localprediction')
        predictlite_version = get_current_container_version('predictlite')
        return {'backend_version': backend_version,
                'frontend_version': frontend_version,
                'prediction_version': prediction_version,
                'predictlite_version': predictlite_version
                }

class SystemIsUptodate(Resource):
    def get(self):
        return all([
            is_container_uptodate('backend')[0], 
            is_container_uptodate('frontend')[0], 
            is_container_uptodate('prediction')[0], 
            is_container_uptodate('predictlite')[0]
        ])

class DeviceInfo(Resource):
    def get(self):
        domain = request.headers.get('Host').split(':')[0]
        info = {}
        info['system']        = system_info()
        info['arch']          = system_arch()
        info['mac_id']        = get_mac_id()
        info['last_active']   = str(datetime.datetime.now())
        info['last_known_ip'] = domain
        return info

class SaveImage(Resource):
    #@auth.requires_auth
    def post(self):
        data = request.json
        path = os.environ['HOME']+'/'+'stored_images'
        usb  = list_usb_paths()[-1]

        cmd_output = subprocess.Popen(['sudo', 'lsblk', '-o', 'MOUNTPOINT', '-nr', usb], stdout=subprocess.PIPE)
        usb_mountpoint = cmd_output.communicate()[0].decode('utf-8')

        if '/boot/efi' in usb_mountpoint:
            print('CANNOT EXPORT TO BOOT MOUNTPOINT')
            return False

        if 'img' in data:
            img_path   = path+'/flexible_vision/snapshots'
            decode_img = base64.b64decode(data['img'])

            if not os.path.exists(img_path):
                os.system('sudo mkdir -p ' + img_path)

            img_path = img_path + '/' +str(int(datetime.datetime.now().timestamp()*1000))
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
        cmd_output = subprocess.Popen(['sudo', 'lsblk', '-o', 'MOUNTPOINT', '-nr', last_connected_usb_path], stdout=subprocess.PIPE)
        usb_mountpoint = cmd_output.communicate()[0].decode('utf-8')

        if '/boot/efi' in usb_mountpoint:
            print('CANNOT EXPORT TO BOOT MOUNTPOINT')
            return False

        usb = usbs[-1].split('/')[-1]
        if usb[0] == 's':
            os.system('sudo mount /dev/' + usb + ' ' + path)

            if 'img' and 'model' and 'version'  in data:
                did = ''
                base_path = path + '/flexible_vision/' + data['model'] + '/' + data['version']

                img_path = base_path + '/images'
                if not os.path.exists(img_path):
                    os.system('sudo mkdir -p ' + img_path)

                if 'inference' in data:
                    inference = data['inference']
                    if 'did' in inference:
                        did = '_'+inference['did']

                img_path   = img_path + '/'+ data['timestamp'].replace(' ', '_').replace('.', '_').replace(':', '-')+did+'.jpg'
                decode_img = base64.b64decode(data['img'])

                with open(img_path, 'wb') as fh:
                    print('writing to: ', img_path)
                    fh.write(decode_img)

                # --------------- export inference data ----------------------

                if 'inference' in data:
                    inference = data['inference']
                    #create inferences folder and add assets
                    inferences_path = base_path + '/inferences'
                    if not os.path.exists(inferences_path):
                        os.system('sudo mkdir -p '+ inferences_path)

                    file_path = inferences_path + '/'
                    if 'did' in inference:
                        did = '_'+inference['did']

                    file_path = file_path+data['timestamp'].replace(' ', '_').replace('.', '_').replace(':', '-')+did+'.json'

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
        if len(eth_names) > 0:
            interface_name = eth_names[-1]
        else:
            return 'ethernet interface not found'
        
        if data['ip'] != '' and is_valid_ip(data['ip']):
            set_static_ips(data['ip'])
            os.system('sudo ifconfig ' + interface_name + ' '  + data['ip'] + ' netmask 255.255.255.0')

        interface = subprocess.Popen(['ifconfig', interface_name], stdout=subprocess.PIPE).communicate()[0].decode('utf-8')

        if 'inet' in interface:
            ip = interface.split('inet')[1].split(' ')[1]
        else:
            ip = 'LAN IP not assigned'

        return ip

class GetLanIps(Resource):
    def get(self):
        lanIps   = {'LAN1': 'not assigned', 'LAN2': 'not assigned'}
        
        eth_names = get_eth_port_names()
        for idx, eth in enumerate(eth_names):
            idx += 1
            lan_port = 'LAN'+str(idx)
            interface = subprocess.Popen(['ifconfig', eth], stdout=subprocess.PIPE).communicate()[0].decode('utf-8')
            
            ip6 = None
            if 'inet6' in interface:
                ip6 = interface.split('inet6')[1].split(' ')[1]
            if 'inet' in interface:
                ip = interface.split('inet')[1].split(' ')[1]
            else:
                ip = 'LAN IP not assigned'

            if ip6 and ip6 == ip: ip = 'LAN IP not assigned'
            lanIps[lan_port] = ip
    
        return lanIps

class UploadModel(Resource):
    #@auth.requires_auth
    def post(self):
        fl = request.files['file']
        if not fl: return False 

        split_fname = fl.filename.split('#')
        model_name  = split_fname[0]
        version     = split_fname[1].split('.')[0]

        #Temporarily write folder to root directory
        path = "/"+model_name
        os.system("mkdir "+path)
        fn = tempfile.gettempdir() + 'model.zip'
        fl.save(fn)

        try:
            print('EXTRACTING ZIP FILE')
            with zipfile.ZipFile(fn) as zf:
                zf.extractall(path)

            os.system("mv "+path+"/job.json "+path+"/"+str(version))
            os.system("mv "+path+"/object-detection.pbtxt "+path+"/"+str(version))
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
            analytics = get_next_analytics_batch()
            if analytics:
                num_data  = len(analytics)
                j_push    = job_queue.enqueue(push_analytics_to_cloud, CLOUD_DOMAIN, access_token, job_timeout=99999999, result_ttl=-1)
                if j_push: insert_job(j_push.id, 'Syncing_'+str(num_data)+'_with_cloud')


api.add_resource(AuthToken, '/auth_token')
api.add_resource(Networks, '/networks')
api.add_resource(MacId, '/mac_id')
api.add_resource(Shutdown, '/shutdown')
api.add_resource(Restart, '/restart')
api.add_resource(Upgrade, '/upgrade')
api.add_resource(CategoryIndex, '/category_index/<string:model>/<string:version>')
api.add_resource(DownloadModels, '/download_models')
api.add_resource(SystemVersions, '/system_versions')
api.add_resource(SystemIsUptodate, '/system_uptodate')
api.add_resource(DeviceInfo, '/device_info')
api.add_resource(SaveImage, '/save_img')
api.add_resource(ExportImage, '/export_img')
api.add_resource(UpdateIp, '/update_ip')
api.add_resource(GetLanIps, '/get_lan_ips')
api.add_resource(GetCameraUID, '/camera_uid/<string:idx>')
api.add_resource(TogglePin, '/toggle_pin')
api.add_resource(RestartBackend, '/refresh_backend')
api.add_resource(UploadModel, '/upload_model')
api.add_resource(AddFtpUser, '/add_ftp_user')
api.add_resource(DeleteFtpUser, '/delete_ftp_user')
api.add_resource(UpdateFtpPort, '/update_ftp_port')
api.add_resource(EnableFtp, '/enable_ftp')
api.add_resource(SyncAnalytics, '/sync_analytics')
api.add_resource(UpgradeFlexRun, '/upgrade_flex_run')

if __name__ == '__main__':
    app.run(host='0.0.0.0',port='5001')
