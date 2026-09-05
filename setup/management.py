import json
import os
from scripts.name_generator import generate_name

CLOUD = {
    "environ": "cloud",
    "use_aws": False,
    "auth0_CID": "512rYG6XL32k3uiFg38HQ8fyubOOUUKf",
    "auth0_domain": "auth.flexiblevision.com",
    "cloud_domain": "https://v1.cloud.flexiblevision.com",
    "branch": "master",
    "gcp_functions_domain": "https://functions-proxy.flexiblevision.com/",
    "container_check_domain": "https://functions-proxy.flexiblevision.com/",
    "interface_name": "enp0s31f6",
    "latest_stable_ref": "latest_stable_version",
    "static_ip": "192.168.10.35",
    "system_user": "visioncell",
    "jwt_secret_key": "123",
    "auth_alg": "RS256",
    "use_mqtt": False,
    "fire_operator": {"db_name": "pod-inspection", "document": "", "trigger_dest": "http://172.17.0.1:1880/trigger"}
}

LOCAL = {
    "environ": "local",
    "use_aws": False,
    "auth0_CID": "123",
    "auth0_domain": "flexiblevision.auth0.com",
    "cloud_domain": "http://localhost",
    "branch": "master",
    "gcp_functions_domain": "http://localhost/api/capture/functions/",
    "container_check_domain": "https://functions-proxy.flexiblevision.com/",
    "interface_name": "enp0s31f6",
    "latest_stable_ref": "latest_stable_version",
    "static_ip": "192.168.10.35",
    "system_user": "visioncell",
    "auth_alg": "HS256",
    "jwt_secret_key": "123",
    "use_mqtt": False
}

# Which cloud a device talks to, and which release channel it follows.
#
# dev points at clouddeploy and follows the beta channel, so a device under
# test takes a release before the fleet does. prod is the default because an
# unattended install must not opt itself into pre-release software.
RELEASE_TRACKS = {
    'prod': {
        'latest_stable_ref': 'latest_stable_version',
        'release_channel': 'stable',
    },
    'dev': {
        'cloud_domain': 'https://clouddeploy.api.flexiblevision.com',
        'latest_stable_ref': 'latest_stable_version_dev',
        'release_channel': 'beta',
    },
}


def generate_environment_config(environment='cloud', override=False,
                                release_track='prod'):
    config = dict(CLOUD)
    if environment == 'local':
        config = dict(LOCAL)

    if release_track not in RELEASE_TRACKS:
        raise ValueError(
            'unknown release track {!r} - expected one of {}'.format(
                release_track, ', '.join(sorted(RELEASE_TRACKS))))
    config.update(RELEASE_TRACKS[release_track])
    config['release_track'] = release_track

    PATH = os.environ['HOME']+'/fvconfig.json'
    if os.path.exists(PATH) and not override:
        print ("CONFIG EXISTS - DOING NOTHING")
    else:
        config['ssid'] = generate_name()
        with open(PATH, 'w') as outfile:  
            json.dump(config, outfile, indent=4, sort_keys=True)
        print('CONFIG CREATED FOR {} ENVIRONMENT'.format(environment))

def update_config(config):
    PATH = os.environ['HOME']+'/fvconfig.json'
    if os.path.exists(PATH):
        with open(PATH, 'w') as outfile:  
            json.dump(config, outfile, indent=4, sort_keys=True)

generate_environment_config()