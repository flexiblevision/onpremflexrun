import requests
import subprocess

CONTAINERS  = {'backend':'capdev', 'frontend':'captureui', 'prediction':'localprediction'}
CLOUD_FUNCTIONS_BASE = 'https://us-central1-flexible-vision-staging.cloudfunctions.net/'

def get_current_container_version(container):
    cmd = subprocess.Popen(['docker', 'inspect', "--format='{{.Config.Image}}'", container], stdout=subprocess.PIPE)
    cmd_out, cmd_err = cmd.communicate()
    if cmd_err:
        print(cmd_err)
        return False
    base_data = cmd_out.strip().decode("utf-8")
    if not base_data: return False
    data = base_data.split(':')[1].replace("'", "")
    return data

def parse_latest_tag(repo_tags, container):
    tags = []
    if 'results' in repo_tags:
        res = repo_tags['results']
        res.sort(key=lambda x:x['last_updated'], reverse=True)
        for repo in res:
            if repo['name'] != 'latest': tags.append(repo['name'])
    else:
        print(container + ' version not found - returning true')
        return (True,str(True))

    latest_version = tags[0]
    system_version = get_current_container_version(CONTAINERS[container])
    is_up_to_date  = latest_version == system_version
    upgrade_to_version = latest_version if is_up_to_date==False else True
    return (is_up_to_date, str(upgrade_to_version))

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


print(get_latest_container_version('backend'))
