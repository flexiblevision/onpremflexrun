import requests
import settings
import os
import json 
import subprocess
from pymongo import MongoClient, ASCENDING
from bson import json_util, ObjectId

client        = MongoClient("172.17.0.1")
interfaces_db = client["fvonprem"]["interfaces"]
PATH = os.environ['HOME']+'/fvconfig.json'

def write_settings_to_config():
    config = settings.config
    with open(PATH, 'w') as outfile:  
        json.dump(config, outfile, indent=4, sort_keys=True)
    print('SETTINGS WRITTEN TO CONFIG')

def add_ports_to_env(interfaces):
    eth_names = [i['iname'] for i in interfaces]
    str_names = " ".join(eth_names)
    body  = "# Defaults for isc-dhcp-server (sourced by /etc/init.d/isc-dhcp-server)\n\n"
    body += "INTERFACESv4=\"{}\"\n".format(str_names)
    body += "INTERFACESv6=\"\""

    path='/etc/default/isc-dhcp-server' 
    with open(path, 'w') as filetowrite:
        filetowrite.write(body)

def write_interfaces_config(interfaces):
    ports = [i['iname'] for i in interfaces]
    body  = "auto lo\niface lo inet loopback\n\n"
    for p in ports: body += "auto " + p + "\n"

    path='/etc/network/interfaces' 
    with open(path, 'w') as filetowrite:
        filetowrite.write(body)

def setup_port_subnets(interfaces):
    body  = "# dhcpd.conf\n\n"
    body += "option domain-name \"example.org\";\n"
    body += "option domain-name-servers ns1.example.org, ns2.example.org;\n"
    body += "default-lease-time 2630000;\n"
    body += "max-lease-time 9999999;\n"
    body += "ddns-update-style none;\n"
    body += "authoritative;\n\n"

    for idx, p in enumerate(interfaces):
        subnet = p['ip'].split(".")[2]
        body += "subnet 192.168."+ str(subnet) +".0 netmask 255.255.255.0 {\n"
        body += "  range 192.168.{}.50 192.168.{}.150;\n".format(subnet, subnet)
        body += "}"

    path='/etc/dhcp/dhcpd.conf' 
    with open(path, 'w') as filetowrite:
        filetowrite.write(body)

def restart_service():
    status = subprocess.check_output("systemctl restart isc-dhcp-server.service", shell=True)
    return status

def set_dhcp():
    res = interfaces_db.find({'dhcp': False})
    interfaces = json.loads(json_util.dumps(res))

    # /etc/default/isc-dhcp-server
    add_ports_to_env(interfaces)
    # /etc/network/interfaces
    write_interfaces_config(interfaces)
    # /etc/dhcp/dhcpd.conf
    setup_port_subnets(interfaces)

    restart_service()