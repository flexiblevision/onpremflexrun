import os
import platform
import subprocess
import requests
import time
import csv
from datetime import datetime
import json


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

    # Add other system information
    info['system'] = system_info.system
    info['node_name'] = system_info.node
    info['release'] = system_info.release
    info['version'] = system_info.version
    info['machine'] = system_info.machine
    info['processor'] = system_info.processor

    service_stats = get_service_stats()
    info['services'] = service_stats
    if save:
        save_metrics_to_csv(info)

    return info

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
