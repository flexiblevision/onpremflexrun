import os
import re
import json
import subprocess
import datetime
from pymongo import MongoClient

client = MongoClient("172.17.0.1")
interfaces_db = client["fvonprem"]["interfaces"]

JUMBO_MTU = 9000
STANDARD_MTU = 1500
# ICMP payload that fills a jumbo frame: 20 bytes IP header, 8 ICMP
JUMBO_PROBE = JUMBO_MTU - 28

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

def remove_conflicting_netplans():
    """Remove conflicting ethernet netplan configs, preserving wifi configs."""
    netplan_dir = '/etc/netplan/'
    if not os.path.exists(netplan_dir):
        return
    for f in os.listdir(netplan_dir):
        if f.endswith('.yaml') and f != 'fv-net-init.yaml':
            filepath = os.path.join(netplan_dir, f)
            try:
                with open(filepath, 'r') as fh:
                    contents = fh.read()
                if 'wifis:' in contents:
                    continue
                os.remove(filepath)
                print(f'Removed conflicting netplan: {filepath}')
            except Exception as e:
                print(f'Failed to remove {filepath}: {e}')


def max_mtu(iname):
    try:
        out = subprocess.run(['ip', '-d', '-j', 'link', 'show', iname],
                             capture_output=True, text=True, timeout=5).stdout
        return json.loads(out)[0].get('max_mtu')
    except Exception:
        return None

def link_peers(iname):
    try:
        out = subprocess.run(['ip', '-4', '-j', 'neigh', 'show', 'dev', iname],
                             capture_output=True, text=True, timeout=5).stdout
        return [n['dst'] for n in json.loads(out) if n.get('lladdr')]
    except Exception:
        return []

def _ping(iname, peer, size=None, tries=1):
    cmd = ['ping', '-c', '1', '-W', '1', '-I', iname]
    if size:
        cmd += ['-M', 'do', '-s', str(size)]
    for _ in range(tries):
        if subprocess.run(cmd + [peer], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0:
            return True
    return False

def carries_jumbo(iname, peer):
    # A peer that does not answer at all says nothing about the MTU. Retried
    # because the link renegotiates after netplan apply.
    if not _ping(iname, peer, tries=5):
        return True
    return _ping(iname, peer, JUMBO_PROBE, tries=2)

def pick_mtu(iname, stored=None):
    if stored:
        return stored
    return JUMBO_MTU if (max_mtu(iname) or 0) >= JUMBO_MTU else STANDARD_MTU

def _write_netplan(stanzas):
    with open ('/etc/netplan/fv-net-init.yaml', 'w') as f:
        f.write('network:\n')
        f.write('  version: 2\n')
        f.write('  renderer: NetworkManager\n')
        f.write('  ethernets:')
        for s in stanzas:
            f.write('\n    '+s['iname']+':\n')
            f.write('      dhcp4: false\n')
            f.write('      mtu: {}\n'.format(s['mtu']))
            f.write('      addresses: '+s['ip_string'])
    os.system("sudo netplan apply")

def apply_netplan(stanzas):
    """Jumbo frames only where the NIC takes them and every peer on the link
    passes one. A switch or peer that drops oversized frames returns no ICMP,
    so TCP stalls on the first full-size segment instead of shrinking."""
    for s in stanzas:
        s['mtu'] = pick_mtu(s['iname'], s.get('mtu'))
    peers = {s['iname']: link_peers(s['iname']) for s in stanzas}

    _write_netplan(stanzas)

    dropped = [s for s in stanzas if s['mtu'] == JUMBO_MTU and
               not all(carries_jumbo(s['iname'], p) for p in peers[s['iname']])]
    if not dropped:
        return
    for s in dropped:
        print('{} drops jumbo frames, using MTU {}'.format(s['iname'], STANDARD_MTU))
        s['mtu'] = STANDARD_MTU
        # Stored so the next rebuild does not go back to 9000 and break the link again.
        interfaces_db.update_one({'iname': s['iname']}, {'$set': {'mtu': STANDARD_MTU}})
    _write_netplan(stanzas)

def set_static_ips(network=None):
    ips = []
    interface_name = get_interface_name_ref()
    if is_valid_ip(network):
        ips.append(network+'/24')

    ip_string = '['
    for ip in ips: ip_string += (ip)
    ip_string = ip_string + ']'

    remove_conflicting_netplans()
    apply_netplan([{'iname': interface_name, 'ip_string': ip_string}])

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
        remove_conflicting_netplans()
        apply_netplan([{'iname': i['iname'], 'ip_string': i['ip_string'], 'mtu': i.get('mtu')}
                       for i in interfaces])
    else:
        print('netplan path does not exist')

def store_netplan_settings(i_config):
    try:
        iname, ip, dhcp = i_config['lanPort'], i_config['ip'], i_config.get('dhcp', True)

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
        interface = subprocess.Popen(['ifconfig', eth], stdout=subprocess.PIPE).communicate()[0].decode('utf-8')
        i_entry = interfaces_db.find_one({'iname': eth})
        # `dhcp` is the setting, on until the operator turns it off. dhcpd only
        # serves ports with a stored entry, so `dhcp_serving` is what is real.
        lanIps['dhcp'] = i_entry.get('dhcp', True) if i_entry else True
        lanIps['dhcp_serving'] = bool(i_entry and i_entry.get('dhcp', True))

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
                    data = {'ip': ip, 'lanPort': eth, 'dhcp': True}
                    if data['ip'] != '' and is_valid_ip(data['ip']):
                        set_ips(data)
                        os.system('sudo ifconfig ' + eth + ' ' + data['ip'] + ' netmask 255.255.255.0')

        if ip6 and ip6 == ip: ip = 'LAN IP not assigned'
        lanIps['ip'] = ip
        lans.append(lanIps)

    return lans
