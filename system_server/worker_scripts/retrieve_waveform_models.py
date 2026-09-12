"""
Sync waveform (stage 2) classifier packages from the cloud to the audio addon.

Separate from retrieve_models for the same reason the anomaly worker is: none of
that worker's substance applies. A waveform package is a zip of a .pth, a label
map and a manifest - there is no saved_model/, no object-detection.pbtxt, no
TensorFlow Serving model.config and no docker cp.

Delivery is the addon's bind mount. The package is unpacked under
<data>/models/<project>/<version>/ on the host, which is <container>/models/...
inside the audio service, and the service picks it up by stat'ing the directory.
Nothing here restarts the container: that would kill every detection session
mid-run, and unlike localprediction those sessions are stateful.

A package is project-shaped and the audio service is device-shaped, so the last
step binds: every device bound to the project gets `cloud_classifier` written on
its record. That binding is what the deploy gate reads.
"""
import datetime
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile

import requests
from pymongo import MongoClient
from rq import get_current_job

from worker_scripts.retrieve_models import save_models_versions

settings_path = os.environ['HOME'] + '/flex-run'
sys.path.append(settings_path)
import settings

client            = MongoClient("172.17.0.1")
job_collection    = client["fvonprem"]["jobs"]
device_collection = client["fvonprem"]["signal_devices"]

CLOUD_DOMAIN = settings.config.get('cloud_domain', "https://clouddeploy.api.flexiblevision.com")

MODEL_TYPE = 'waveform'
METHOD     = 'waveform_classifier'
MANIFEST   = 'manifest.json'

# Where the addon mounts its data. Read from the descriptor rather than repeated
# here, so moving the mount does not silently strand the sync.
ADDON_JSON = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), 'addons', 'catalog', 'anomaly_audio', 'addon.json')
FALLBACK_HOST_DATA      = '/home/visioncell/Documents/audio_anomaly_data'
FALLBACK_CONTAINER_DATA = '/app/data'

INVALID_STEM_CHARS = re.compile(r'[^A-Za-z0-9_-]')
LEADING_NON_ALNUM  = re.compile(r'^[^A-Za-z0-9]+')
MAX_PROJECT_STEM   = 60


def data_dirs():
    """(host, container) sides of the audio addon's data mount."""
    try:
        with open(ADDON_JSON) as fh:
            for vol in (json.load(fh).get('container') or {}).get('volumes') or []:
                if vol.get('container') and vol.get('host'):
                    return vol['host'], vol['container']
    except Exception as error:
        print('could not read', ADDON_JSON, error)
    return FALLBACK_HOST_DATA, FALLBACK_CONTAINER_DATA


def update_job_progress(progress):
    job = get_current_job()
    if job:
        job_collection.update_one({'_id': job.id}, {'$set': {'progress': progress}})


def record_job_error(message):
    """fvonprem.jobs is the only place the console can see why a sync did nothing."""
    print(message)
    job = get_current_job()
    if job:
        job_collection.update_one({'_id': job.id},
                                  {'$set': {'error': str(message)[:500]}})


def sanitize_project_name(name):
    stem = INVALID_STEM_CHARS.sub('_', str(name))
    stem = LEADING_NON_ALNUM.sub('', stem)
    return stem[:MAX_PROJECT_STEM]


def download_package(token, project_id, version, destination):
    """The zip for one (project, version). Small enough not to need a signed link."""
    url = '{}/api/capture/models/download/{}/{}'.format(CLOUD_DOMAIN, project_id, version)
    with requests.get(url, headers={'Authorization': 'Bearer ' + token},
                      stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(destination, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    return os.path.getsize(destination)


def read_manifest(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        name = next((n for n in zf.namelist()
                     if os.path.basename(n) == MANIFEST), None)
        if not name:
            raise ValueError('no {} in package'.format(MANIFEST))
        return json.loads(zf.read(name).decode('utf-8'))


def install_package(zip_path, project_dir, version):
    """
    Unpack to <project_dir>/<version>/, replacing any previous copy.

    Staged beside the destination and renamed, so the service never stats a
    half-written package directory. The manifest's `audio` block is NOT checked
    here: the feature contract is the service's to enforce against its own
    config, and a second implementation of that check is how the two drift.
    """
    destination = os.path.join(project_dir, str(version))
    staged = tempfile.mkdtemp(dir=project_dir, prefix='.' + str(version) + '.')
    retired = destination + '.retired'
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(staged)
        if os.path.exists(destination):
            os.replace(destination, retired)
        os.replace(staged, destination)
    except BaseException:
        shutil.rmtree(staged, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(retired, ignore_errors=True)
    return destination


def prune_versions(project_dir, keep_versions):
    """Drop versions the sync plan no longer lists. Only this worker writes here."""
    removed = []
    for name in os.listdir(project_dir):
        path = os.path.join(project_dir, name)
        if not os.path.isdir(path) or name.startswith('.'):
            continue
        if name not in keep_versions:
            shutil.rmtree(path, ignore_errors=True)
            removed.append(name)
    if removed:
        print('pruned', removed)
    return removed


def bind_devices(project_id, version, package_path):
    """
    Point every device on this project at the package that just landed.

    A package belongs to a project; the audio service serves per device. This is
    the join, and `cloud_classifier` is what the deploy gate requires - without
    it a synced model is inert.
    """
    result = device_collection.update_many(
        {'cloud_project.id': project_id},
        {'$set': {'cloud_classifier': {
            'project_id':    project_id,
            'model_version': version,
            'package_path':  package_path,
            'bound_at':      int(datetime.datetime.utcnow().timestamp() * 1000),
        }}})
    print('bound {} device(s) on project {} to version {}'.format(
        result.modified_count, project_id, version))
    return result.modified_count


def retrieve_waveform_models(data, token, cloud_domain=None):
    global CLOUD_DOMAIN
    if cloud_domain:
        CLOUD_DOMAIN = cloud_domain
    print('syncing waveform models from', CLOUD_DOMAIN)

    models = data.get('models') or {}
    if not models:
        record_job_error('no waveform models in sync plan')
        return False

    # An empty exclude_models means "re-download everything" — the intent Clean &
    # Sync and Full Sync carry for every other type.
    force_redownload = not bool(data.get('exclude_models'))

    host_data, container_data = data_dirs()
    models_root = os.path.join(host_data, 'models')
    os.makedirs(models_root, exist_ok=True)

    total_versions = sum(len(ref.get('models') or []) for ref in models.values())
    completed      = 0
    synced         = {}
    update_job_progress(0)

    for model_ref in models.values():
        project_id   = model_ref['_id']
        project_name = sanitize_project_name(model_ref['name'])
        versions     = [str(v) for v in (model_ref.get('models') or [])]
        project_dir  = os.path.join(models_root, project_name)
        os.makedirs(project_dir, exist_ok=True)

        for version in versions:
            destination = os.path.join(project_dir, version)

            if os.path.exists(destination) and not force_redownload:
                print('already present, skipping', project_name, version)
                completed += 1
                update_job_progress(round((completed / total_versions) * 100))
                record = synced.setdefault(project_name, {
                    'project_id': project_id, 'versions': [], 'latest': None})
                record['versions'].append(version)
                record['latest'] = version
                continue

            print('Syncing waveform model', project_name, 'version', version)
            fd, payload = tempfile.mkstemp(dir=project_dir, prefix='.payload.', suffix='.zip')
            os.close(fd)
            try:
                download_package(token, project_id, version, payload)
                manifest = read_manifest(payload)
                method = manifest.get('method')
                if method != METHOD:
                    raise ValueError(
                        'package declares method {!r}, expected {!r}'.format(method, METHOD))
                install_package(payload, project_dir, version)
            except Exception as error:
                record_job_error('failed to sync {} version {}: {}'.format(
                    project_name, version, error))
                completed += 1
                update_job_progress(round((completed / total_versions) * 100))
                continue
            finally:
                if os.path.exists(payload):
                    os.unlink(payload)

            completed += 1
            update_job_progress(round((completed / total_versions) * 100))

            record = synced.setdefault(project_name, {
                'project_id': project_id, 'versions': [], 'latest': None})
            record['versions'].append(version)
            record['latest'] = version

        if synced.get(project_name):
            prune_versions(project_dir, set(synced[project_name]['versions']))

    if not synced:
        record_job_error('no waveform packages landed — see errors above')
        return False

    save_models_versions(
        [{'type': name, MODEL_TYPE: record['versions']} for name, record in synced.items()],
        MODEL_TYPE)

    # The path the SERVICE sees, not the host's: it reads this from inside the
    # container, where the same directory is mounted at container_data.
    for project_name, record in synced.items():
        latest = record['latest']
        if not latest:
            continue
        bind_devices(record['project_id'], latest,
                     os.path.join(container_data, 'models', project_name, latest))

    update_job_progress(100)
    return True
