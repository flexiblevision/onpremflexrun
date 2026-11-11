import requests
import os
import sys
import json
import time
import fcntl
import subprocess
import ipaddress
from contextlib import contextmanager
from pymongo import MongoClient, ASCENDING
from bson import json_util, ObjectId

client        = MongoClient("172.17.0.1")
interfaces_db = client["fvonprem"]["interfaces"]
PATH = os.environ['HOME']+'/fvconfig.json'

def write_settings_to_config():
    # Imported here: settings regenerates the environment config on import,
    # which the once-a-minute DHCP reconcile has no business doing.
    import settings
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


def has_ipv4(name):
    """NetworkManager strips a port's address when its link drops and puts it
    back on relink. dhcpd ignores a port with no address on it."""
    try:
        out = subprocess.run(['ip', '-j', '-4', 'addr', 'show', 'dev', name],
                             capture_output=True, text=True, timeout=5).stdout
        return any(a.get('local') for i in json.loads(out or '[]')
                   for a in i.get('addr_info', []))
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def port_ready(name):
    return interface_is_up(name) and has_ipv4(name)


# netplan apply resets every port it touches, but returns before the links drop.
# They come back 3-10s later and can flap once on the way, so a port only counts
# once it has stayed ready for SETTLE_SECONDS.
SETTLE_SECONDS = 3
WAIT_SECONDS   = 20


def wait_for_ports(names, timeout=WAIT_SECONDS, settle=SETTLE_SECONDS):
    deadline = time.monotonic() + timeout
    ready_since = None
    while True:
        now = time.monotonic()
        if all(port_ready(n) for n in names):
            if ready_since is None:
                ready_since = now
            if now - ready_since >= settle:
                return True
        else:
            ready_since = None
        if now >= deadline:
            return False
        time.sleep(0.5)


LOCK_PATH = '/run/fv-dhcp.lock'


@contextmanager
def dhcp_lock(blocking=True):
    """Serialises a settings save and the cron reconcile, which would otherwise
    write the dhcpd files over each other. Yields whether the lock is held."""
    try:
        handle = open(LOCK_PATH, 'w')
    except OSError:
        yield True
        return
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        handle.close()


def served_ports():
    try:
        with open('/etc/default/isc-dhcp-server') as f:
            for line in f:
                if line.startswith('INTERFACESv4='):
                    return set(line.split('=', 1)[1].strip().strip('"').split())
    except OSError:
        pass
    return set()


def service_active():
    return subprocess.run(['systemctl', 'is-active', '--quiet', 'isc-dhcp-server.service'],
                          capture_output=True, text=True).returncode == 0


def default_route_ports():
    """Ports a default route leaves by: the plant network, where a second DHCP
    server hands out addresses that take other machines off it."""
    try:
        out = subprocess.run(['ip', '-j', 'route', 'show', 'default'],
                             capture_output=True, text=True, timeout=5).stdout
        return {r['dev'] for r in json.loads(out or '[]') if r.get('dev')}
    except (OSError, ValueError, subprocess.SubprocessError):
        return set()


def dhcp_candidates():
    res = interfaces_db.find({'dhcp': True})
    interfaces = json.loads(json_util.dumps(res))

    uplinks = default_route_ports()
    refused = [i.get('iname') for i in interfaces if i.get('iname') in uplinks]
    if refused:
        print('dhcp: not serving {} - default route leaves by it'.format(', '.join(refused)))
    return [i for i in interfaces if i.get('iname') not in uplinks]


def set_dhcp(wait_for=()):
    """wait_for: ports that had a link before a netplan apply, so are expected
    back. Without the wait they were judged mid-reset and dropped from dhcpd."""
    with dhcp_lock():
        interfaces = dhcp_candidates()
        expected = [i['iname'] for i in interfaces if i.get('iname') in wait_for]
        if expected and not wait_for_ports(expected):
            print('dhcp: {} not back after {}s'.format(', '.join(expected), WAIT_SECONDS))
        _serve(interfaces)


def reconcile_dhcp():
    """Run from cron. Restarts dhcpd only when a port it should serve is ready
    and not being served - after a boot, a replug, or a save that outran the
    wait. Returns whether it acted."""
    with dhcp_lock(blocking=False) as held:
        if not held:
            return False
        interfaces = dhcp_candidates()
        wanted = {i['iname'] for i in interfaces
                  if subnet_block(i) and port_ready(i.get('iname', ''))}
        if not wanted or (wanted <= served_ports() and service_active()):
            return False
        print('dhcp: reconciling, {} ready but not served'.format(
            ', '.join(sorted(wanted - served_ports()) or sorted(wanted))))
        _serve(interfaces)
        return True


def _serve(interfaces):
    # The stored setting is left alone: a port keeps "serve DHCP here" while
    # its cable is out, and serves again when it is plugged back in. Only what
    # is handed to dhcpd is filtered, because a port that is down cannot be
    # listened on and its presence stops the whole service starting.
    serving = [i for i in interfaces if port_ready(i.get('iname', ''))]

    skipped = [i.get('iname') for i in interfaces if i not in serving]
    if skipped:
        print('dhcp: not serving {} - port(s) down or without an address'.format(', '.join(skipped)))

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


if __name__ == '__main__' and sys.argv[1:] == ['reconcile']:
    reconcile_dhcp()