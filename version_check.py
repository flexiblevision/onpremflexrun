import requests
import subprocess

CONTAINERS  = {'backend':'capdev', 'frontend':'captureui', 'prediction':'localprediction'}

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

def is_container_uptodate(container):
    repo_tags = requests.get('https://registry.hub.docker.com/v2/repositories/fvonprem/arm-'+container+'/tags/').json()
    return parse_latest_tag(repo_tags, container)

