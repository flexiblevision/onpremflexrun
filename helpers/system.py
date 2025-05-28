import os
import platform
import subprocess
import requests
import time

def get_system_metrics():
    """
    Get system metrics such as CPU and memory usage.
    """
    info = {}  
    system_info = platform.uname()

    info['cpu']     = 100-int(subprocess.check_output(["vmstat 1 2|tail -1|awk '{print $15}'"], shell=True).decode('utf-8').strip())
    info['memory']  = int(float(subprocess.check_output(["free | grep Mem | awk '{print $3/$2 * 100.0}'"], shell=True).decode('utf-8').strip()))
    info['storage'] = int(float(subprocess.check_output(["df -h --total | grep total | awk '{print $5}'"], shell=True).decode('utf-8').strip().split('%')[0]))
    try:
        info['gpu'] = int(float(subprocess.check_output(["nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader"], shell=True).decode('utf-8').strip().split('%')[0]))
    except subprocess.CalledProcessError:
        info['gpu'] = 0
    info['system'] = system_info.system
    info['node_name'] = system_info.node
    info['release'] = system_info.release
    info['version'] = system_info.version
    info['machine'] = system_info.machine
    info['processor'] = system_info.processor

    return info
