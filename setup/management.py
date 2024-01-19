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
    "gcp_functions_domain": "https://us-central1-flexible-vision-staging.cloudfunctions.net/",
    "container_check_domain": "https://us-central1-flexible-vision-staging.cloudfunctions.net/",
    "interface_name": "enp0s31f6",
    "latest_stable_ref": "latest_stable_version",
    "static_ip": "192.168.10.35",
    "system_user": "visioncell",
    "jwt_secret_key": "123",
    "auth_alg": "RS256"
}

LOCAL = {
    "environ": "local",
    "use_aws": False,
    "auth0_CID": "123",
    "auth0_domain": "flexiblevision.auth0.com",
    "cloud_domain": "http://localhost",
    "branch": "master",
    "gcp_functions_domain": "http://localhost/api/capture/functions/",
    "container_check_domain": "https://us-central1-flexible-vision-staging.cloudfunctions.net/",
    "interface_name": "enp0s31f6",
    "latest_stable_ref": "latest_stable_version",
    "static_ip": "192.168.10.35",
    "system_user": "visioncell",
    "auth_alg": "HS256",
    "jwt_secret_key": "123"
}

def generate_environment_config(environment='cloud', override=False):
    config = CLOUD
    if environment == 'local':
        config = LOCAL

    PATH = os.environ['HOME']+'/fvconfig.json'
    if os.path.exists(PATH) and not override:
        print ("CONFIG EXISTS - DOING NOTHING")
    else:
        config['ssid'] = generate_name()
        with open(PATH, 'w') as outfile:  
            json.dump(config, outfile, indent=4, sort_keys=True)
        print('CONFIG CREATED FOR {} ENVIRONMENT'.format(environment))


