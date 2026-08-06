import os
import json

DEFAULT_CLOUD_DOMAIN = "https://v1.cloud.flexiblevision.com"
DEFAULT_FUNCTIONS_BASE = "https://functions-proxy.flexiblevision.com/"

_utils_coll = None


def _local_config():
    try:
        with open(os.environ['HOME'] + '/fvconfig.json') as f:
            cfg = json.load(f)
        if cfg.get('environ') == 'local':
            return cfg
    except Exception:
        pass
    return None


def _master_ip_from_db():
    # In local/client mode the master address lives in the client_mode utility
    # record (the same source cameras/prediction/system use).
    global _utils_coll
    try:
        if _utils_coll is None:
            from pymongo import MongoClient
            client = MongoClient(
                os.environ.get('MONGO_SERVER', '172.17.0.1'),
                int(os.environ.get('MONGO_PORT', 27017)),
                serverSelectionTimeoutMS=2000,
            )
            _utils_coll = client['fvonprem']['utils']
        rec = _utils_coll.find_one({'type': 'client_mode'})
        ip = (rec or {}).get('config', {}).get('master_ip')
        if ip:
            return ip if ip.startswith(('http://', 'https://')) else 'http://' + ip
    except Exception:
        pass
    return None


def _cloud_base():
    cfg = _local_config()
    if not cfg:
        return None
    return _master_ip_from_db() or cfg.get('cloud_domain')


def get_cloud_domain(fallback=DEFAULT_CLOUD_DOMAIN):
    return _cloud_base() or fallback


def get_cloud_functions_base(fallback=DEFAULT_FUNCTIONS_BASE):
    base = _cloud_base()
    return base.rstrip('/') + '/api/capture/functions/' if base else fallback
