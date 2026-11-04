import requests
import subprocess
import os
import settings

CONTAINERS  = {
    'backend':'capdev',
    'frontend':'captureui',
    'prediction':'localprediction',
    'predictlite': 'predictlite',
    'vision': 'vision',
    'nodecreator': 'nodecreator',
    'visiontools': 'visiontools',
    # Not upgraded through the positional arguments, but a release pins it like
    # any other component - and a component missing from here is never seen as
    # current, so it is torn down and recreated on every run.
    'vernemq': 'vernemq'
}

CLOUD_FUNCTIONS_BASE = settings.config['container_check_domain'] if 'container_check_domain' in settings.config else 'https://functions-proxy.flexiblevision.com/'
LATEST_STABLE_REF    = settings.config['latest_stable_ref'] if 'latest_stable_ref' in settings.config else 'latest_stable_version'

def parse_image_reference(reference):
    """The tag a container is running, or its sha256:... when it has no tag.

    Splitting on the first ':' returned the digest hex for a container created
    from repo@sha256:... - which is every container after a signed release
    pins one. Nothing then matched a manifest tag, so the next release read the
    whole stack as changed and recreated containers that were already correct.

    Split from the right, and only after the last '/', so a registry host with
    a port (host:5000/image:tag) is not mistaken for a tag.
    """
    reference = (reference or '').strip().strip("'\"")
    if not reference:
        return False

    if '@' in reference:
        return reference.split('@', 1)[1]

    name = reference.rsplit('/', 1)[-1]
    if ':' not in name:
        return False
    return name.rsplit(':', 1)[1]


def get_current_container_version(container):
    cmd = subprocess.Popen(['docker', 'inspect', "--format='{{.Config.Image}}'", container], stdout=subprocess.PIPE)
    cmd_out, cmd_err = cmd.communicate()
    if cmd_err:
        return False
    base_data = cmd_out.strip().decode("utf-8")
    if not base_data: return False
    return parse_image_reference(base_data)

def get_latest_image_versions(image):
    data    = {"arch": system_arch(), "image": image}
    headers = {"Content-Type": "application/json"} 
    res     = requests.post(CLOUD_FUNCTIONS_BASE+'container_versions_list', json=data, headers=headers)
    
    if res:
        return res.json()

def stable_ref():
    """Which version endpoint to ask, resolved per call.

    A device that switched track at runtime has to ask the ref of the track it
    is on now, not the one baked in when this module was imported.
    """
    try:
        import cloud_env
        return cloud_env.get_latest_stable_ref(LATEST_STABLE_REF)
    except Exception:
        return LATEST_STABLE_REF

def latest_stable_image_version(image):
    data    = {"arch": system_arch(), "image": image}
    headers = {"Content-Type": "application/json"}
    res     = requests.post(CLOUD_FUNCTIONS_BASE+stable_ref(), json=data, headers=headers)
    if res.status_code == 200:
        return res.text

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
