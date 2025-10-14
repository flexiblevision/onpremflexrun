import os
import subprocess
import datetime
import platform
from flask import request
from flask_restx import Resource
from utils.device_utils import get_mac_id, system_info, system_arch
from utils.network_utils import get_lan_ips
from helpers.system import get_system_metrics
import settings

if platform.processor() != 'aarch64':
    from gpio.gpio_helper import toggle_pin, set_pin_state, read_pin

class MacId(Resource):
    def get(self):
        return get_mac_id()

class DeviceInfo(Resource):
    def get(self):
        info = {}
        domain = request.headers.get('Host').split(':')[0]
        ifconfig = subprocess.Popen(['ifconfig'], stdout=subprocess.PIPE).communicate()[0].decode('utf-8')
        interface = 'wl' + ifconfig.split('wl')[1].split(':')[0]
        wlp = subprocess.Popen(['ifconfig', interface], stdout=subprocess.PIPE).communicate()[0].decode('utf-8')

        if 'inet' in wlp:
            info['last_known_ip'] = wlp.split('inet')[1].split(' ')[1]
        else:
            info['last_known_ip'] = domain

        lan_ips = get_lan_ips()
        for lan in lan_ips:
            ip = lan['ip']
            if ip != 'not assigned' and ip != 'LAN IP not assigned':
                info['last_known_ip'] = '{};{}'.format(ip, info['last_known_ip'])

        info['system'] = system_info()
        info['arch'] = system_arch()
        info['mac_id'] = get_mac_id()
        info['hotspot'] = settings.config['ssid'] if 'ssid' in settings.config else 'not configured'
        info['last_active'] = str(datetime.datetime.now())
        info['metrics'] = get_system_metrics()

        return info

class GetCameraUID(Resource):
    def get(self, idx):
        out = subprocess.Popen(['udevadm', 'info', '--query=all', '/dev/video'+idx], stdout=subprocess.PIPE)
        cmd = subprocess.Popen(['grep', 'VENDOR_ID\|MODEL_ID\|SERIAL_SHORT'], stdin=out.stdout, stdout=subprocess.PIPE)
        cmd_out, cmd_err = cmd.communicate()
        uid = cmd_out.strip().decode("utf-8")
        msv = uid.splitlines()
        uid = ''
        for i, did in enumerate(msv):
            uid += did.split('=')[-1]
            if i < len(msv)-1: uid += ':'
        return uid

class TogglePin(Resource):
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

class ReadPin(Resource):
    def post(self):
        j = request.json
        if 'pin_num' in j:
            return read_pin(j['pin_num'])
        else:
            return -1

def register_routes(api):
    api.add_resource(MacId, '/mac_id')
    api.add_resource(DeviceInfo, '/device_info')
    api.add_resource(GetCameraUID, '/camera_uid/<string:idx>')
    api.add_resource(TogglePin, '/toggle_pin')
    api.add_resource(SetPin, '/set_pin')
    api.add_resource(ReadPin, '/read_input_pin')
