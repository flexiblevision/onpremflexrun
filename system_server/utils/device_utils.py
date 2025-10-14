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

def system_info():
    out = subprocess.Popen(['lshw', '-short'], stdout=subprocess.PIPE)
    cmd = subprocess.Popen(['grep', 'system'], stdin=out.stdout, stdout=subprocess.PIPE)
    cmd_out, cmd_err = cmd.communicate()
    system = cmd_out.strip().decode("utf-8")
    system = " ".join(system.split())
    return system

def system_arch():
    cmd = subprocess.Popen(['arch'], stdout=subprocess.PIPE)
    cmd_out, cmd_err = cmd.communicate()
    return cmd_out.strip().decode("utf-8")

def list_usb_paths():
    valid_formats = ['vfat', 'exfat']
    mount_paths = []
    for format_type in valid_formats:
        usb_list = subprocess.Popen(['sudo', 'blkid', '-t', 'TYPE='+format_type, '-o', 'device'], stdout=subprocess.PIPE)
        usb = usb_list.communicate()[0].decode('utf-8').splitlines()
        if len(usb) > 0:
            usb = usb[-1].split('/')[-1]
            mount_paths.append(usb)

    return mount_paths

def base_path():
    xavier_ssd = '/xavier_ssd/'
    return xavier_ssd if os.path.exists(xavier_ssd) else '/'
