import os
import re
import subprocess
import datetime
from pymongo import MongoClient

client = MongoClient("172.17.0.1")
interfaces_db = client["fvonprem"]["interfaces"]

def is_valid_ip(ip):
    if not ip: return False
    m = re.match(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$", ip)
    return bool(m) and all(map(lambda n: 0 <= int(n) <= 255, m.groups()))

def get_static_ip_ref():
    import settings
    static_ip = settings.config['static_ip'] if 'static_ip' in settings.config else '192.168.10.35'
    return static_ip

def get_interface_name_ref():
    eth_ports = get_eth_port_names()
    if len(eth_ports) <= 1:
        interface_name = 'enp0s31f6'
    else:
        interface_name = get_eth_port_names()[-1]
    return interface_name

def set_static_ips(network=None):
    ips = []
    interface_name = get_interface_name_ref()
    if is_valid_ip(network):
        ips.append(network+'/24')

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
    from helpers.config_helper import set_dhcp
    set_dhcp()

def build_set_netplan():
    import json
    from bson import json_util

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

def get_lan_ips():
    lans = []
    eth_names = get_eth_port_names()
    for idx, eth in enumerate(eth_names):
        lanIps = {}
        idx += 1
        lan_port = 'LAN'+str(idx)
        lanIps['ip'] = 'not assigned'
        lanIps['port'] = eth
        lanIps['name'] = lan_port
        lanIps['dhcp'] = False
        interface = subprocess.Popen(['ifconfig', eth], stdout=subprocess.PIPE).communicate()[0].decode('utf-8')
        i_entry = interfaces_db.find_one({'iname': eth})
        if i_entry: lanIps['dhcp'] = i_entry['dhcp']

        ip6 = None
        ip = 'LAN IP not assigned'
        if 'inet6' in interface:
            ip6 = interface.split('inet6')[1].split(' ')[1]
        if 'inet' in interface:
            ip = interface.split('inet')[1].split(' ')[1]
        else:
            if idx > 2:
                if not i_entry:
                    ip = '192.168.{}.10'.format(5+idx)
                    data = {'ip': ip, 'lanPort': eth, 'dhcp': False}
                    if data['ip'] != '' and is_valid_ip(data['ip']):
                        set_ips(data)
                        os.system('sudo ifconfig ' + eth + ' ' + data['ip'] + ' netmask 255.255.255.0')

        if ip6 and ip6 == ip: ip = 'LAN IP not assigned'
        lanIps['ip'] = ip
        lans.append(lanIps)

    return lans
