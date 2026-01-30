import os
import platform
import subprocess
import requests
import time
import csv
from datetime import datetime
import json
import re


MIN_VALID_YEAR = 2020
REBOOT_THRESHOLD_MS = 600000  # 5 min


def get_system_metrics(save=False):
    """
    Get system metrics such as CPU and memory usage.
    """
    info = {}
    system_info = platform.uname()

    try:
        info['cpu']     = 100-int(subprocess.check_output(["vmstat 1 2|tail -1|awk '{print $15}'"], shell=True).decode('utf-8').strip())
    except (subprocess.CalledProcessError, ValueError):
        info['cpu'] = 0

    try:
        info['memory']  = int(float(subprocess.check_output(["free | grep Mem | awk '{print $3/$2 * 100.0}'"], shell=True).decode('utf-8').strip()))
    except (subprocess.CalledProcessError, ValueError):
        info['memory'] = 0

    try:
        info['storage'] = int(float(subprocess.check_output(["df -h --total | grep total | awk '{print $5}'"], shell=True).decode('utf-8').strip().split('%')[0]))
    except (subprocess.CalledProcessError, ValueError, IndexError):
        info['storage'] = 0

    try:
        info['gpu'] = int(float(subprocess.check_output(["nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader"], shell=True).decode('utf-8').strip().split('%')[0]))
    except subprocess.CalledProcessError:
        info['gpu'] = 0

    try:
        with open('/proc/uptime', 'r') as f:
            uptime_seconds = float(f.readline().split()[0])
            info['uptime_seconds'] = int(uptime_seconds)
            days, remainder = divmod(uptime_seconds, 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, _ = divmod(remainder, 60)
            info['uptime'] = f"{int(days)}d {int(hours)}h {int(minutes)}m"
    except (IOError, ValueError, IndexError):
        info['uptime_seconds'] = 0
        info['uptime'] = '0d 0h 0m'

    # Add other system information
    info['system'] = system_info.system
    info['node_name'] = system_info.node
    info['release'] = system_info.release
    info['version'] = system_info.version
    info['machine'] = system_info.machine
    info['processor'] = system_info.processor

    service_stats = get_service_stats()
    info['services'] = service_stats
    info['shutdown_events'] = get_shutdown_events()
    if save:
        save_metrics_to_csv(info)

    return info

def get_current_boot_time():
    try:
        result = subprocess.check_output(
            ["uptime", "-s"],
            stderr=subprocess.PIPE
        ).decode('utf-8').strip()
        dt = datetime.strptime(result, '%Y-%m-%d %H:%M:%S')
        return int(dt.timestamp() * 1000)
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        return None


def get_shutdown_events(limit=20):
    events = []
    
    try:
        result = subprocess.check_output(
            ["last", "-x", "-F", "shutdown", "reboot"],
            stderr=subprocess.PIPE
        ).decode('utf-8')

        date_pattern = re.compile(
            r'(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+'
            r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+'
            r'(\d{1,2})\s+'
            r'(\d{2}:\d{2}:\d{2})\s+'
            r'(\d{4})'
        )

        duration_pattern = re.compile(r'\((-?\d+)\+(\d{2}):(\d{2})\)|\((\d{2}):(\d{2})\)')

        raw_events = []
        for line in result.strip().splitlines():
            if not line or 'wtmp begins' in line:
                continue

            parts = line.split()
            if not parts or parts[0] not in ('shutdown', 'reboot'):
                continue

            matches = date_pattern.findall(line)
            if not matches:
                continue

            first_date_str = ' '.join(matches[0])
            is_running = 'still running' in line
            
            duration_ms = None
            duration_match = duration_pattern.search(line)
            if duration_match:
                if duration_match.group(1) is not None:
                    days = abs(int(duration_match.group(1)))
                    hours = int(duration_match.group(2))
                    minutes = int(duration_match.group(3))
                    duration_ms = ((days * 24 + hours) * 60 + minutes) * 60 * 1000
                else:
                    hours = int(duration_match.group(4))
                    minutes = int(duration_match.group(5))
                    duration_ms = (hours * 60 + minutes) * 60 * 1000
            
            try:
                first_dt = datetime.strptime(first_date_str, '%a %b %d %H:%M:%S %Y')
                first_ts = int(first_dt.timestamp() * 1000)
                first_valid = first_dt.year >= MIN_VALID_YEAR
                
                if is_running and not first_valid:
                    first_ts = get_current_boot_time()
                    first_valid = first_ts is not None
                
                if first_valid:
                    raw_events.append({
                        'raw_type': parts[0],
                        'timestamp_ms': first_ts,
                        'duration_ms': duration_ms
                    })
            except ValueError:
                continue

        for event in raw_events:
            if event['raw_type'] == 'shutdown':
                event_type = 'shutdown'
            else:
                if event['duration_ms'] is None or event['duration_ms'] > REBOOT_THRESHOLD_MS:
                    event_type = 'startup'
                else:
                    event_type = 'reboot'
            
            events.append({
                'type': event_type,
                'timestamp_ms': event['timestamp_ms']
            })
            
            if len(events) >= limit:
                break

    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    return events


def get_service_stats():
    command = "docker stats --no-stream --format '{{json .}}'"
    container_stats = {}

    try:
        result = subprocess.check_output(command, shell=True, stderr=subprocess.PIPE).decode('utf-8')
        lines = result.strip().splitlines()
        for line in lines:
            if line: 
                try:                   
                    stats_dict = json.loads(line)
                    container_stats[stats_dict['Name']] = stats_dict
                except json.JSONDecodeError as e:
                    print(f"Warning: Could not decode JSON from line: {line}. Error: {e}")

    except subprocess.CalledProcessError as e:
        error_message = e.stderr.decode('utf-8').strip()
        if "Cannot connect to the Docker daemon" in error_message:
            print("Warning: Docker daemon is not running.")
        else:
            print(f"Warning: Error executing 'docker stats': {error_message}")
    except FileNotFoundError:
        print("Warning: 'docker' command not found. Is Docker installed?")

    return container_stats


def save_metrics_to_csv(metrics_data, filename="/home/visioncell/Documents/system_metrics.csv", limit=5000):
    fieldnames = [
        'timestamp', 'cpu', 'memory', 'storage', 'gpu',
        'system', 'node_name', 'release', 'version', 'machine', 'processor'
    ]

    metrics_data['timestamp'] = datetime.now().isoformat()

    rows = []
    file_exists = os.path.exists(filename)

    if file_exists:
        with open(filename, 'r', newline='') as csvfile:
            reader = csv.DictReader(csvfile)
            if reader.fieldnames == fieldnames:
                rows = list(reader)

    rows.append(metrics_data)

    if len(rows) > limit:
        rows = rows[-limit:]

    with open(filename, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
