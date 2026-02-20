import os
import platform
import subprocess
import requests
import time
import csv
from datetime import datetime
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pymongo import MongoClient

utils_db = MongoClient("172.17.0.1")["fvonprem"]["utils"]

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
    info['metadata'] = get_metadata()
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


def get_metadata():
    """Get device metadata such as TeamViewer ID."""
    metadata = {}
    try:
        tv_output = subprocess.check_output(
            ['sudo', 'teamviewer', 'info'], stderr=subprocess.PIPE, timeout=10
        ).decode('utf-8')
        for line in tv_output.splitlines():
            if 'TeamViewer ID' in line:
                raw = line.split(':')[-1].strip()
                metadata['teamviewer_id'] = re.sub(r'\x1b\[[0-9;]*m', '', raw).strip()
                break
    except Exception:
        pass

    metadata['software_version'] = get_software_versions()

    # Sync state from utils db
    try:
        sync_state = {}
        for doc in utils_db.find({'type': {'$in': ['sync', 'sync_interval', 'purge_analytics', 'purge_interval']}}, {'_id': 0}):
            doc_type = doc.pop('type')
            sync_state[doc_type] = doc
        metadata['sync_state'] = sync_state
    except Exception:
        pass

    return metadata


# Maps docker container name -> cloud image key for latest version lookup
_CLOUD_IMAGE_KEY = {
    'capdev': 'backend',
    'captureui': 'frontend',
    'localprediction': 'prediction',
    'predictlite': 'predictlite',
    'vision': 'vision',
    'nodecreator': 'nodecreator',
    'visiontools': 'visiontools'
}


def get_software_versions():
    """Get running vs latest version for all running containers."""
    # 1) Get all running containers + image tags in a single docker call
    try:
        result = subprocess.run(
            ['docker', 'ps', '--format', '{{.Names}}\t{{.Image}}'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return {}
    except Exception:
        return {}

    running = {}
    for line in result.stdout.strip().splitlines():
        parts = line.split('\t', 1)
        if len(parts) == 2:
            name = parts[0]
            running[name] = parts[1].split(':')[-1] if ':' in parts[1] else None

    if not running:
        return {}

    # 2) Single request to get all latest stable versions from cloud
    stable_versions = {}
    arch = 'x86'
    try:
        arch_raw = subprocess.check_output(['arch'], timeout=5).decode('utf-8').strip()
        arch = {'aarch64': 'arm', 'x86_64': 'x86'}.get(arch_raw, arch_raw)

        with open(os.path.join(os.environ['HOME'], 'fvconfig.json')) as f:
            config = json.load(f)
        cloud_base = config.get('container_check_domain', 'https://us-central1-flexible-vision-staging.cloudfunctions.net/')
        stable_ref = config.get('latest_stable_ref', 'latest_stable_version')

        resp = requests.post(
            cloud_base + stable_ref,
            json={"arch": arch},
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        if resp.status_code == 200:
            stable_versions = resp.json()
    except Exception:
        pass

    # 3) Build result — match running containers to their latest stable version
    versions = {}
    for name, running_tag in running.items():
        cloud_key = _CLOUD_IMAGE_KEY.get(name)
        latest = stable_versions.get(f'{arch}-{cloud_key}') if cloud_key else None
        versions[name] = {
            'running': running_tag,
            'latest': latest if latest else running_tag,
        }

    return versions


def get_presets():
    """
    Fetch list of presets from the local capture service.
    """
    try:
        response = requests.get('http://172.17.0.1/api/capture/io/', timeout=5)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"Warning: Could not fetch presets: {e}")
        return []


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
