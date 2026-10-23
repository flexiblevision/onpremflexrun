"""Hotspot SSID generation.

The name is derived from the device's hardware identity rather than picked at
random, so two devices on one floor cannot end up broadcasting the same SSID
and a device keeps its name across a reimage.

The identity chain mirrors scripts/serial_number.sh: motherboard serial,
product serial, CPU serial, then MAC address. The serial is hashed rather than
used directly - an SSID is broadcast to anyone in range, and the raw serial is
what the asset tag and support tooling key on.
"""
import hashlib
import os
import random

SSID_PREFIX = 'visioncell_'
SUFFIX_LENGTH = 6

# Values OEMs ship in the DMI fields when they have not been programmed.
PLACEHOLDER_SERIALS = {
    '', 'NONE', 'NOTSPECIFIED', 'DEFAULTSTRING', 'TOBEFILLEDBYOEM',
}

DMI_PATH = '/sys/class/dmi/id'
NET_PATH = '/sys/class/net'
CPUINFO_PATH = '/proc/cpuinfo'


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ''


def _normalise(value):
    return value.replace(' ', '').upper()


def _dmi_serial(field):
    value = _normalise(_read(os.path.join(DMI_PATH, field)))
    return '' if value in PLACEHOLDER_SERIALS else value


def _cpu_serial():
    for line in _read(CPUINFO_PATH).splitlines():
        key, _, value = line.partition(':')
        if key.strip().lower() == 'serial':
            value = _normalise(value)
            # ARM boards that have no serial report all zeroes.
            if value and set(value) != {'0'}:
                return value
    return ''


def _mac_address():
    try:
        interfaces = sorted(os.listdir(NET_PATH))
    except OSError:
        return ''

    # eno1/eth0 first to match serial_number.sh, then any other real interface.
    preferred = [i for i in ('eno1', 'eth0') if i in interfaces]
    for interface in preferred + [i for i in interfaces if i != 'lo']:
        value = _normalise(_read(os.path.join(NET_PATH, interface, 'address')))
        value = value.replace(':', '')
        if value and set(value) != {'0'}:
            return value
    return ''


def device_serial():
    """The device's stable hardware identity, or '' if none is readable."""
    for source in (lambda: _dmi_serial('board_serial'),
                   lambda: _dmi_serial('product_serial'),
                   _cpu_serial,
                   _mac_address):
        value = source()
        if value:
            return value
    return ''


def generate_name():
    """The device's hotspot SSID.

    Deterministic for a given machine. Falls back to a random suffix when no
    hardware identity can be read, so first-boot configuration never fails -
    such a device gets a working, if unstable, name.
    """
    serial = device_serial()
    if serial:
        suffix = hashlib.sha1(serial.encode('utf-8')).hexdigest()[:SUFFIX_LENGTH]
    else:
        suffix = '%0*x' % (SUFFIX_LENGTH, random.getrandbits(SUFFIX_LENGTH * 4))

    return SSID_PREFIX + suffix
