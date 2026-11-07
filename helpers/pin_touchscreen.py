#!/usr/bin/env python3
"""Pin every touchscreen to one monitor in the GNOME session.

Unpinned, mutter maps touch to whichever monitor's EDID size matches the panel,
which on dual-screen cells is the second screen, and re-guesses on every hotplug.

Target: --output, else /etc/flexrun/touch_output, else the primary monitor.
Without an explicit target it only acts with 2+ monitors, and keeps an existing
pin whose monitor is still connected.
"""
import argparse
import glob
import os
import subprocess
import sys
import time

from gi.repository import Gio, GLib

SCHEMA = 'org.gnome.desktop.peripherals.touchscreen'
OVERRIDE = '/etc/flexrun/touch_output'


def log(msg):
    print(f'pin_touchscreen: {msg}', file=sys.stderr)


def touchscreens():
    found = set()
    for node in glob.glob('/sys/class/input/event*'):
        props = subprocess.run(['udevadm', 'info', '-q', 'property', '-p', node],
                               capture_output=True, text=True).stdout
        if 'ID_INPUT_TOUCHSCREEN=1' not in props.splitlines():
            continue
        ids = os.path.join(node, 'device', 'id')
        try:
            with open(os.path.join(ids, 'vendor')) as v, open(os.path.join(ids, 'product')) as p:
                found.add((v.read().strip().lower(), p.read().strip().lower()))
        except OSError:
            continue
    return sorted(found)


def display_state():
    # Mutter's cached state; unlike `xrandr` without --current this never
    # re-probes the outputs, which can drop a marginal DP link.
    bus = Gio.bus_get_sync(Gio.BusType.SESSION)
    for _ in range(30):
        try:
            res = bus.call_sync('org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig',
                                'org.gnome.Mutter.DisplayConfig', 'GetCurrentState', None, None,
                                Gio.DBusCallFlags.NONE, 5000, None)
            _, monitors, logical, _ = res.unpack()
            specs = [tuple(m[0]) for m in monitors]
            primary = [tuple(s) for lm in logical if lm[4] for s in lm[5]][:1]
            return specs, (primary[0] if primary else None)
        except GLib.Error:
            time.sleep(1)
    log('mutter DisplayConfig not available')
    sys.exit(0)


def explicit_output(arg):
    if arg:
        return arg
    try:
        with open(OVERRIDE) as f:
            return f.read().strip() or None
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--output', help=f'connector to pin to, e.g. DP-1 (default: {OVERRIDE}, else primary)')
    ap.add_argument('--clear', action='store_true', help='remove the pin and let mutter guess again')
    args = ap.parse_args()

    devices = touchscreens()
    if not devices:
        log('no touchscreen found')
        return

    target, value = 'auto', ['', '', '']
    explicit = explicit_output(args.output)
    if not args.clear:
        specs, primary = display_state()
        if explicit:
            match = [s for s in specs if s[0] == explicit]
            if not match:
                log(f'no monitor {explicit}; have {[s[0] for s in specs]}')
                return
            spec = match[0]
        elif len(specs) < 2:
            log('single monitor, mutter maps it correctly; leaving as is')
            return
        elif primary:
            spec = primary
        else:
            log('no primary monitor; leaving as is')
            return
        if sum(1 for s in specs if s[1:] == spec[1:]) > 1:
            log(f'warning, {spec[0]} shares vendor/product/serial with another monitor; mutter may pick either')
        target, *value = spec

    for vid, pid in devices:
        settings = Gio.Settings.new_with_path(SCHEMA, f'/org/gnome/desktop/peripherals/touchscreens/{vid}:{pid}/')
        current = list(settings.get_strv('output'))
        if not args.clear and not explicit and any(current):
            if any(list(s[1:]) == current for s in specs):
                log(f'{vid}:{pid} already pinned to {current}; keeping')
                continue
            log(f'{vid}:{pid} pinned to {current}, which is not connected; repinning')
        settings.set_strv('output', value)
        log(f'{vid}:{pid} -> {target} {value}')
    Gio.Settings.sync()


if __name__ == '__main__':
    main()
