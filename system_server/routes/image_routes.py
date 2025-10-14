import os
import json
import base64
import time
import datetime
import subprocess
from flask import request
from flask_restx import Resource
from utils.device_utils import list_usb_paths

class SaveImage(Resource):
    def post(self):
        data = request.json
        path = os.environ['HOME']+'/'+'stored_images'
        usb = list_usb_paths()[-1]

        cmd_output = subprocess.Popen(['sudo', 'lsblk', '-o', 'MOUNTPOINT', '-nr', '/dev/'+usb], stdout=subprocess.PIPE)
        usb_mountpoint = cmd_output.communicate()[0].decode('utf-8')

        if '/boot/efi' in usb_mountpoint or usb_mountpoint == '':
            print('CANNOT EXPORT TO BOOT MOUNTPOINT')
            return False

        if 'img' in data:
            img_path = path+'/flexible_vision/snapshots'
            todayDate = time.strftime("%d-%m-%y")
            img_path = img_path +'/' + todayDate
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

            if 'img' and 'model' and 'version' in data:
                did = ''
                base_path = path + '/flexible_vision/' + data['model'] + '/' + data['version']

                img_path = base_path + '/images'
                todayDate = time.strftime("%d-%m-%y")
                img_path = img_path +'/' + todayDate

                if not os.path.exists(img_path):
                    os.makedirs(img_path)

                if 'inference' in data:
                    inference = data['inference']
                    if 'did' in inference:
                        did = '_'+inference['did']

                timestamp = str(datetime.datetime.now())
                img_path = img_path + '/'+ timestamp.replace(' ', '_').replace('.', '_').replace(':', '-')+did+'.jpg'
                decode_img = base64.b64decode(data['img'])

                with open(img_path, 'wb') as fh:
                    print('writing to: ', img_path)
                    fh.write(decode_img)

                if 'inference' in data:
                    inference = data['inference']
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

def register_routes(api):
    api.add_resource(SaveImage, '/save_img')
    api.add_resource(ExportImage, '/export_img')
