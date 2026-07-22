import os
import json

DEFAULT_CLOUD_DOMAIN = "https://v1.cloud.flexiblevision.com"
DEFAULT_FUNCTIONS_BASE = "https://functions-proxy.flexiblevision.com/"

def _local_config():
    try:
        with open(os.environ['HOME'] + '/fvconfig.json') as f:
            cfg = json.load(f)
        if cfg.get('environ') == 'local':
            return cfg
    except Exception:
        pass
    return None

def get_cloud_domain(fallback=DEFAULT_CLOUD_DOMAIN):
    cfg = _local_config()
    if cfg and cfg.get('cloud_domain'):
        return cfg['cloud_domain']
    return fallback

def get_cloud_functions_base(fallback=DEFAULT_FUNCTIONS_BASE):
    cfg = _local_config()
    if cfg and cfg.get('cloud_domain'):
        return cfg['cloud_domain'].rstrip('/') + '/api/capture/functions/'
    return fallback
