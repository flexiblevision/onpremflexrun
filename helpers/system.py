import os
import platform
import subprocess
import requests
import time
import csv
from datetime import datetime

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
    if save:
        save_metrics_to_csv(info)

    return info

def save_metrics_to_csv(metrics_data, filename="/home/visioncell/Documents/system_metrics.csv"):
    """
    Appends system metrics to a CSV file.
    Creates the file and writes headers if it doesn't exist.
    """
    fieldnames = [
        'timestamp',
        'cpu',
        'memory',
        'storage',
        'gpu',
        'system',
        'node_name',
        'release',
        'version',
        'machine',
        'processor'
    ]

    file_exists = os.path.exists(filename)
    
    with open(filename, 'a', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        
        if not file_exists:
            writer.writeheader()        
        metrics_data['timestamp'] = datetime.now().isoformat()        
        writer.writerow(metrics_data)