import os
import subprocess

def get_mac_id():
    ifconfig = subprocess.Popen(['ifconfig'], stdout=subprocess.PIPE).communicate()[0].decode('utf-8')
    if 'wl' in ifconfig:
        interface = 'wl' + ifconfig.split('wl')[1].split(':')[0]
    elif 'enp' in ifconfig:
        interface = 'enp' + ifconfig.split('enp')[1].split(':')[0]
    else:
        return None

    cmd = subprocess.Popen(['cat', '/sys/class/net/'+interface+'/address'], stdout=subprocess.PIPE)
    cmd_out, cmd_err = cmd.communicate()
    return cmd_out.strip().decode("utf-8")

def _device_id_path():
    return os.environ.get('FLEXRUN_DEVICE_ID_PATH', '/etc/flexrun/device_id')


def _read_pinned():
    try:
        with open(_device_id_path()) as handle:
            return handle.readline().strip()
    except OSError:
        return ''


def _pin(device_id):
    path = _device_id_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        staged = path + '.tmp'
        with open(staged, 'w') as handle:
            handle.write(device_id + '\n')
        os.replace(staged, path)
    except OSError as error:
        print(f"[device_serial] could not pin {device_id} to {path}: {error}")


def _derive():
    script = os.path.join(os.environ['HOME'], 'flex-run', 'scripts', 'serial_number.sh')
    try:
        out = subprocess.Popen([script], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return out.communicate()[0].decode('utf-8').strip()
    except Exception as error:
        print(f"[device_serial] serial_number.sh failed: {error}")
        return ''


def device_serial(stored_id=None, stored_known=True):
    """This system's one ID, fixed the first time it is resolved.

    serial_number.sh falls back to the first NIC's MAC, so re-deriving after a
    deauthorize moved a device onto another port's address and orphaned its
    workstation bindings and history. The first answer is pinned and returned
    forever after; a device already registered is pinned to the ID it
    registered as. When the stored ID could not be read, the derived value is
    reported but not pinned, since it may not be what the device registered as.
    """
    pinned = _read_pinned()
    if pinned:
        return pinned
    stored_id = (stored_id or '').strip()
    serial = stored_id or _derive()
    if serial and stored_known:
        _pin(serial)
    return serial


# Hardware and arch don't change while the process runs. Empty results aren't
# cached so a failed lshw is retried.
_static = {}

def system_info():
    if _static.get('system'):
        return _static['system']
    out = subprocess.Popen(['lshw', '-short'], stdout=subprocess.PIPE)
    cmd = subprocess.Popen(['grep', 'system'], stdin=out.stdout, stdout=subprocess.PIPE)
    cmd_out, cmd_err = cmd.communicate()
    system = cmd_out.strip().decode("utf-8")
    system = " ".join(system.split())
    _static['system'] = system
    return system

def system_arch():
    if _static.get('arch'):
        return _static['arch']
    cmd = subprocess.Popen(['arch'], stdout=subprocess.PIPE)
    cmd_out, cmd_err = cmd.communicate()
    arch = cmd_out.strip().decode("utf-8")
    _static['arch'] = arch
    return arch

_usb_cache = {'paths': [], 'time': 0}

def list_usb_paths():
    import time
    now = time.time()
    if now - _usb_cache['time'] < 5:
        return _usb_cache['paths']

    valid_formats = ['vfat', 'exfat']
    mount_paths = []
    for format_type in valid_formats:
        usb_list = subprocess.Popen(['sudo', 'blkid', '-t', 'TYPE='+format_type, '-o', 'device'], stdout=subprocess.PIPE)
        usb = usb_list.communicate()[0].decode('utf-8').splitlines()
        if len(usb) > 0:
            usb = usb[-1].split('/')[-1]
            mount_paths.append(usb)

    _usb_cache['paths'] = mount_paths
    _usb_cache['time'] = now
    return mount_paths

def base_path():
    xavier_ssd = '/xavier_ssd/'
    return xavier_ssd if os.path.exists(xavier_ssd) else '/'
