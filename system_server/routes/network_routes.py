import os
import subprocess
from flask import request
from flask_restx import Resource
import auth
from utils.network_utils import get_eth_port_names, is_valid_ip, set_ips, get_lan_ips, restart_network_manager

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

        for i, line in enumerate(network_list):
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
            set_ips(data)
            os.system('sudo ifconfig ' + interface_name + ' ' + data['ip'] + ' netmask 255.255.255.0')
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

def register_routes(api):
    api.add_resource(Networks, '/networks')
    api.add_resource(UpdateIp, '/update_ip')
    api.add_resource(GetLanIps, '/get_lan_ips')
