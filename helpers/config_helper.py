import requests
import settings
import os
import json 
import subprocess
import ipaddress
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

# Where a port's pool starts and ends, counted from the network address. On a
# /24 this is the .50 to .150 the hardcoded version produced, so existing rigs
# keep the range they have always had.
POOL_FIRST_OFFSET = 50
POOL_LAST_OFFSET  = 150


def subnet_block(entry):
    """One dhcpd subnet block for an interface, or None if its address cannot
    be read.

    The address is taken as written, rather than assuming 192.168.x.0/24. The
    previous version read only the third octet and rebuilt the rest from a
    literal "192.168.", so a port set to 10.0.5.10 was served a pool on
    192.168.5.0 - a network the port is not on, which hands cameras addresses
    they cannot be reached at.
    """
    raw = str(entry.get('ip') or '').strip()
    if not raw:
        return None
    try:
        # Stored with the prefix, as "192.168.9.10/24". A bare address is
        # treated as /24, which is what every rig uses today.
        iface = ipaddress.ip_interface(raw if '/' in raw else raw + '/24')
    except ValueError:
        return None

    net = iface.network
    # Leave room for the network and broadcast addresses at either end; a
    # prefix shorter than the offsets would otherwise run past the subnet.
    usable = int(net.num_addresses) - 2
    if usable < 2:
        return None

    first_off = min(POOL_FIRST_OFFSET, usable)
    last_off  = min(POOL_LAST_OFFSET, usable)
    if last_off <= first_off:
        first_off, last_off = 1, usable

    base  = int(net.network_address)
    first = ipaddress.ip_address(base + first_off)
    last  = ipaddress.ip_address(base + last_off)

    return (
        "subnet {} netmask {} {{\n"
        "  range {} {};\n"
        "}}\n"
    ).format(net.network_address, net.netmask, first, last)


def setup_port_subnets(interfaces):
    body  = "# dhcpd.conf\n\n"
    body += "option domain-name \"example.org\";\n"
    body += "option domain-name-servers ns1.example.org, ns2.example.org;\n"
    body += "default-lease-time 2630000;\n"
    body += "max-lease-time 9999999;\n"
    body += "ddns-update-style none;\n"
    body += "authoritative;\n\n"

    for entry in interfaces:
        block = subnet_block(entry)
        # A port whose address will not parse is skipped rather than written
        # as a broken block: one bad entry used to be enough to stop dhcpd
        # reading the file at all.
        if block:
            body += block

    path='/etc/dhcp/dhcpd.conf'
    with open(path, 'w') as filetowrite:
        filetowrite.write(body)

def restart_service():
    # check_output raises on a non-zero exit, so a service that would not start
    # became an exception in the middle of applying network settings - by which
    # point the address had already been changed.
    return subprocess.run(['systemctl', 'restart', 'isc-dhcp-server.service'],
                          capture_output=True, text=True).returncode == 0

def stop_service():
    ok = subprocess.run(['systemctl', 'stop', 'isc-dhcp-server.service'],
                        capture_output=True, text=True).returncode == 0
    # A unit that previously failed to start stays "failed" after being
    # stopped, which reads as a fault on a machine that simply has no wired
    # port to serve. Clearing it leaves the honest state, "inactive".
    subprocess.run(['systemctl', 'reset-failed', 'isc-dhcp-server.service'],
                   capture_output=True, text=True)
    return ok

def interface_is_up(name):
    """Whether the kernel reports this port as up - for ethernet, whether a
    cable is in it. dhcpd refuses to start unless at least one port it is given
    is up: it exits with "Not configured to listen on any interfaces!", which
    takes DHCP down for the ports that are connected too."""
    try:
        with open('/sys/class/net/{}/operstate'.format(name)) as f:
            return f.read().strip() == 'up'
    except OSError:
        return False


def set_dhcp():
    res = interfaces_db.find({'dhcp': True})
    interfaces = json.loads(json_util.dumps(res))

    # The stored setting is left alone: a port keeps "serve DHCP here" while
    # its cable is out, and serves again when it is plugged back in. Only what
    # is handed to dhcpd is filtered, because a port that is down cannot be
    # listened on and its presence stops the whole service starting.
    serving = [i for i in interfaces if interface_is_up(i.get('iname', ''))]

    skipped = [i.get('iname') for i in interfaces if i not in serving]
    if skipped:
        print('dhcp: not serving {} - port(s) down'.format(', '.join(skipped)))

    # /etc/default/isc-dhcp-server
    add_ports_to_env(serving)
    # /etc/network/interfaces
    write_interfaces_config(serving)
    # /etc/dhcp/dhcpd.conf
    setup_port_subnets(serving)

    if serving:
        restart_service()
    else:
        # Stopping is the honest end state. Restarting into a unit that cannot
        # bind leaves it "failed", which reads as a fault rather than as an
        # unplugged cable.
        print('dhcp: no connected ports configured to serve, stopping')
        stop_service()