import requests
import subprocess
import os

CONTAINERS  = {
    'backend':'capdev', 
    'frontend':'captureui', 
    'prediction':'localprediction',
    'predictlite': 'predictlite'
}
CLOUD_FUNCTIONS_BASE = 'https://us-central1-flexible-vision-staging.cloudfunctions.net/'
gcp_functions_path   = os.path.expanduser('~/flex-run/setup_constants/gcp_functions_domain.txt')
with open(gcp_functions_path, 'r') as file:
    CLOUD_FUNCTIONS_BASE = file.read().replace('\n', '')

LATEST_STABLE_REF  = 'latest_stable_version'
latest_stable_path = os.path.expanduser('~/flex-run/setup_constants/latest_stable_ref.txt')
with open(latest_stable_path, 'r') as file:
    LATEST_STABLE_REF = file.read().replace('\n', '')

def get_current_container_version(container):
    cmd = subprocess.Popen(['docker', 'inspect', "--format='{{.Config.Image}}'", container], stdout=subprocess.PIPE)
    cmd_out, cmd_err = cmd.communicate()
    if cmd_err:
        return False
    base_data = cmd_out.strip().decode("utf-8")
    if not base_data: return False
    data = base_data.split(':')[1].replace("'", "")
    return data


def get_latest_image_versions(image):
    data    = {"arch": system_arch(), "image": image}
    headers = {"Content-Type": "application/json"} 
    res     = requests.post(CLOUD_FUNCTIONS_BASE+'container_versions_list', json=data, headers=headers)
    
    if res:
        return res.json()

def latest_stable_image_version(image):
    data    = {"arch": system_arch(), "image": image}
    headers = {"Content-Type": "application/json"}
    res     = requests.post(CLOUD_FUNCTIONS_BASE+LATEST_STABLE_REF, json=data, headers=headers)
    if res:
        return res.json()

def is_container_uptodate(container):
    system_version  = get_current_container_version(CONTAINERS[container])
    image_versions  = get_latest_image_versions(container)
    stable_version  = latest_stable_image_version(container)

    if str(stable_version) not in image_versions:
        #if stable version does not exist - Do not prompt for upgrade
        return (True,'True')
    
    is_up_to_date = str(stable_version) == str(system_version)
    
    print(f'is up to date {is_up_to_date}')
    print(f'system version {system_version}')
    print(f'lastest stable version {stable_version}')
    upgrade_to_version = stable_version if not is_up_to_date else True

    return (is_up_to_date, str(upgrade_to_version))

def system_arch():
    cmd = subprocess.Popen(['arch'], stdout=subprocess.PIPE)
    cmd_out, cmd_err = cmd.communicate()
    arch = cmd_out.strip().decode("utf-8")
    
    if arch == 'aarch64': arch = 'arm'
    if arch == 'x86_64': arch = 'x86'

    return arch
