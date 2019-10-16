import requests
import subprocess

CONTAINERS  = {'backend':'capdev', 'frontend':'captureui', 'prediction':'localprediction'}
CLOUD_FUNCTIONS_BASE = 'https://us-central1-flexible-vision-staging.cloudfunctions.net/'

def get_current_container_version(container):
    cmd = subprocess.Popen(['docker', 'inspect', "--format='{{.Config.Image}}'", container], stdout=subprocess.PIPE)
    cmd_out, cmd_err = cmd.communicate()
    if cmd_err:
        return False
    base_data = cmd_out.strip().decode("utf-8")
    if not base_data: return False
    data = base_data.split(':')[1].replace("'", "")
    return data


def get_latest_container_version(image):
    data    = {"arch": system_arch(), "image": image}
    headers = {"Content-Type": "application/json"} 
    res     = requests.post(CLOUD_FUNCTIONS_BASE+'container_versions', json=data, headers=headers)
    
    if res:
        return res.json()

def is_container_uptodate(container):
    system_version  = get_current_container_version(CONTAINERS[container])
    latest_version  = get_latest_container_version(container)
    is_up_to_date   = latest_version == system_version
    
    upgrade_to_version = latest_version if is_up_to_date==False else True

    return (is_up_to_date, str(upgrade_to_version))

def system_arch():
    cmd = subprocess.Popen(['arch'], stdout=subprocess.PIPE)
    cmd_out, cmd_err = cmd.communicate()
    arch = cmd_out.strip().decode("utf-8")
    
    if arch == 'aarch64': arch = 'arm'
    if arch == 'x86_64': arch = 'x86'

    return arch

