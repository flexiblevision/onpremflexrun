"""
Sync anomaly model packages from the cloud into the anomaly-server's mount.

Separate from retrieve_models because none of that worker's substance applies to
a single-file package: no zip layout surgery, no saved_model/variables moves, no
object-detection.pbtxt, no TensorFlow Serving model.config, no lite_models split
and no docker cp.

Delivery is a bind mount, not a copy. anomaly-server has /anomaly/models mounted
at /models, and its predictor cache keys on (st_mtime_ns, st_size), so replacing
a package in place is picked up on the next request. Nothing here restarts or
redeploys the container.
"""
import requests
import os
import sys
import re
import json
import glob
import shutil
import tarfile
import tempfile
import zipfile
import datetime
from pymongo import MongoClient
from rq import get_current_job

# Spec step 7: the existing save_models_versions semantics carry over, so the
# fvonprem.models write and the preset version bump are the same code every
# other model type uses. type_map there now carries 'anomaly'.
from worker_scripts.retrieve_models import save_models_versions

settings_path = os.environ['HOME']+'/flex-run'
sys.path.append(settings_path)
import settings

client             = MongoClient("172.17.0.1")
job_collection     = client["fvonprem"]["jobs"]
models_collection  = client["fvonprem"]["models"]
presets_collection = client["fvonprem"]["io_presets"]
anomaly_collection = client["fvonprem"]["anomaly_models"]

CLOUD_DOMAIN = settings.config['cloud_domain'] if 'cloud_domain' in settings.config else "https://clouddeploy.api.flexiblevision.com"

# The host side of anomaly-server's bind mount. A literal, not base_path():
# the deployed container mounts this exact path, and there is no ARM build, so
# the Xavier-SSD case base_path() exists to handle cannot arise here.
ANOMALY_MODELS_DIR = '/anomaly/models'
PACKAGE_EXT        = '.fvmdl'
MODEL_TYPE         = 'anomaly'

# anomaly_pipeline/server.py PKG_RE is
#   ^[A-Za-z0-9][A-Za-z0-9_-]{0,80}\.(fvmdl|fvpkg)$
# so no dots, parens, spaces or percent signs in the stem. format_filename()
# permits all of those, which is why this worker does not reuse it: a project
# named "Panel (v2)" would sync successfully and then 400 on every predict.
# Only packages the device itself named are candidates for pruning. Anything
# else in the directory was put there by hand and is not the sync's to remove.
DEVICE_PACKAGE_RE  = re.compile(r'^.+__\d+\{}$'.format(PACKAGE_EXT))
INVALID_STEM_CHARS = re.compile(r'[^A-Za-z0-9_-]')
LEADING_NON_ALNUM  = re.compile(r'^[^A-Za-z0-9]+')
MAX_PROJECT_STEM   = 60


def update_job_progress(progress):
    job = get_current_job()
    if job:
        job_collection.update_one({'_id': job.id}, {'$set': {'progress': progress}})


def record_job_error(message):
    """
    Surface a failure in the job record.

    fvonprem.jobs is the only place the console can see why a sync did nothing.
    A worker that swallows an error and returns False is indistinguishable from
    'the cloud had nothing to send', which is the single most confusing way this
    can fail.
    """
    print(message)
    job = get_current_job()
    if job:
        job_collection.update_one({'_id': job.id},
                                  {'$set': {'error': str(message)[:500]}})


def sanitize_project_name(name):
    """Project name -> a stem the anomaly server will accept."""
    stem = INVALID_STEM_CHARS.sub('_', str(name))
    stem = LEADING_NON_ALNUM.sub('', stem)
    return stem[:MAX_PROJECT_STEM]


def package_name(project_name, version):
    """
    The device names the package, not training.

    Training names packages <category>_<method>.fvmdl and MODELS_DIR is flat, so
    two projects sharing a category and a lane would collide silently. Keying the
    filename on (project, version) also means a retrain lands beside its
    predecessor and a rollback is a file move.
    """
    return '{}__{}{}'.format(sanitize_project_name(project_name), version, PACKAGE_EXT)


def _stream_to_file(response, destination, on_progress=None):
    written     = 0
    cont_length = int(response.headers.get('Content-length', 0))
    response.raise_for_status()
    with open(destination, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
            written += len(chunk)
            if cont_length > 0 and on_progress:
                on_progress(written / cont_length)
    return written


def download_package(token, project_id, version, destination, on_progress=None):
    """
    Fetch the payload for one (project, version).

    Prefers the signed-link endpoint: anomaly packages run to hundreds of MB and
    a signed URL keeps those bytes off capdev. Falls back to the direct download
    endpoint, which is the path every other model type takes.
    """
    headers = {'Authorization': 'Bearer '+token}
    try:
        link_url = '{}/api/capture/models/download_link/{}/{}'.format(CLOUD_DOMAIN, project_id, version)
        res = requests.get(link_url, headers=headers, timeout=60)
        res.raise_for_status()
        signed_link = res.json()
        if signed_link:
            with requests.get(signed_link, stream=True, timeout=600) as r:
                return _stream_to_file(r, destination, on_progress)
    except Exception as error:
        print('signed link unavailable, falling back to direct download:', error)

    url = '{}/api/capture/models/download/{}/{}'.format(CLOUD_DOMAIN, project_id, version)
    with requests.get(url, headers=headers, stream=True, timeout=600) as r:
        return _stream_to_file(r, destination, on_progress)


def extract_package(payload_path, destination):
    """
    Get a .fvmdl out of whatever the cloud returned.

    A .fvmdl is a gzipped tar, so the two shapes are distinguishable by their
    magic bytes and both are handled: the cloud currently wraps model artifacts
    in a zip, but a bare package needs no unwrapping.
    """
    with open(payload_path, 'rb') as f:
        head = f.read(4)

    if head[:2] == b'PK':
        with zipfile.ZipFile(payload_path) as zf:
            members = [n for n in zf.namelist() if n.lower().endswith(PACKAGE_EXT)]
            if not members:
                raise ValueError('no {} inside {}'.format(PACKAGE_EXT, os.path.basename(payload_path)))
            with zf.open(members[0]) as src, open(destination, 'wb') as out:
                shutil.copyfileobj(src, out)
        return

    if head[:2] == b'\x1f\x8b':
        shutil.copyfile(payload_path, destination)
        return

    raise ValueError('unrecognised payload for {} (starts {!r})'.format(
        os.path.basename(payload_path), head))


def read_manifest(package_path):
    """Best effort — the manifest carries lane, thresholds and metrics."""
    try:
        with tarfile.open(package_path, 'r:gz') as tf:
            for member in tf:
                if os.path.basename(member.name) == 'manifest.json':
                    return json.load(tf.extractfile(member))
    except Exception as error:
        print('could not read manifest from', package_path, error)
    return {}


def install_package(payload_path, models_dir, filename):
    """
    Put the package in place atomically.

    GET /api/models re-globs the directory on every call and the predictor cache
    keys on (mtime, size), so a partially written .fvmdl is both visible and
    loadable. Writing to a temp name in the same directory and renaming makes the
    package appear whole or not at all. The .part suffix also keeps it out of the
    server's *.fvmdl glob before the rename.
    """
    destination = os.path.join(models_dir, filename)
    fd, staged = tempfile.mkstemp(dir=models_dir, prefix='.'+filename+'.', suffix='.part')
    os.close(fd)
    try:
        extract_package(payload_path, staged)
        os.replace(staged, destination)
    except BaseException:
        if os.path.exists(staged):
            os.unlink(staged)
        raise
    return destination


def prune_packages(models_dir, keep_filenames):
    """
    Remove packages the sync plan no longer lists.

    Deliberately not the rm -rf that retrieve_models does on a full resync: this
    directory is the container's live serving directory, not a staging area. It
    holds training_history.jsonl, and a package an operator dropped in by hand
    keeps its own name — so only files matching the device's own
    <project>__<version>.fvmdl naming are eligible, and only after the new ones
    have landed.
    """
    removed = []
    for path in glob.glob(os.path.join(models_dir, '*'+PACKAGE_EXT)):
        name = os.path.basename(path)
        if not DEVICE_PACKAGE_RE.match(name):
            continue
        if name not in keep_filenames:
            try:
                os.unlink(path)
                removed.append(name)
            except OSError as error:
                print('could not remove', path, error)
    if removed:
        print('pruned', removed)
        # The detail records have to go with the files. Leaving them behind means
        # the console offers a model the container cannot serve, and the row
        # never gets cleaned up by anything else.
        anomaly_collection.delete_many({'file': {'$in': removed}})
    return removed


def save_anomaly_versions(synced):
    """
    Record what landed, for two readers.

    fvonprem.models goes through save_models_versions() so an anomaly sync
    behaves like every other type - including its empty-list pruning and the
    preset bump via assign_preset_to_latest_version().

    NOTE: that pruning deletes any model document left with all version lists
    empty. On a device carrying both object detection and anomaly models that
    can evict an unrelated record (it removed UniversalPodInspection on a test
    device here). Kept because the spec calls for these semantics to carry over;
    the fix belongs in save_models_versions, not in a per-type workaround.
    """
    models_versions = [{'type': name, MODEL_TYPE: record['versions']}
                       for name, record in synced.items()]
    if models_versions:
        save_models_versions(models_versions, MODEL_TYPE)

    for model_name, record in synced.items():
        for version, detail in record['packages'].items():
            manifest = detail['manifest']
            anomaly_collection.update_one(
                {'project': model_name, 'model_version': version},
                {'$set': {
                    'project':       model_name,
                    'project_id':    record['project_id'],
                    'model_version': version,
                    'file':          detail['file'],
                    'lane':          manifest.get('method') or manifest.get('lane'),
                    'categories':    manifest.get('categories'),
                    'manifest':      manifest,
                    'synced_at':     datetime.datetime.utcnow(),
                }},
                upsert=True)


def retrieve_anomaly_models(data, token, cloud_domain=None):
    # Resolved per call rather than pinned at import so a caller can target a
    # specific cloud; defaults to the device's configured domain.
    global CLOUD_DOMAIN
    if cloud_domain:
        CLOUD_DOMAIN = cloud_domain
    print('syncing anomaly models from', CLOUD_DOMAIN)

    models = data.get('models') or {}
    if not models:
        record_job_error('no anomaly models in sync plan')
        return False

    # An empty exclude_models means "re-download everything", the same intent
    # Clean & Sync / Full Sync carry for the other model types.
    force_redownload = not bool(data.get('exclude_models'))

    models_dir = ANOMALY_MODELS_DIR
    if not os.path.exists(models_dir):
        os.makedirs(models_dir)

    total_versions   = sum(len(ref.get('models') or []) for ref in models.values())
    completed        = 0
    synced           = {}
    keep_filenames   = set()
    update_job_progress(0)

    for model_ref in models.values():
        project_id   = model_ref['_id']
        project_name = sanitize_project_name(model_ref['name'])
        versions     = model_ref.get('models') or []

        for version in versions:
            filename    = package_name(model_ref['name'], version)
            destination = os.path.join(models_dir, filename)
            keep_filenames.add(filename)

            if os.path.exists(destination) and not force_redownload:
                print('already present, skipping', filename)
                completed += 1
                update_job_progress(round((completed / total_versions) * 100))
                record = synced.setdefault(project_name, {
                    'project_id': project_id, 'versions': [], 'packages': {}})
                record['versions'].append(version)
                known = anomaly_collection.find_one(
                    {'project': project_name, 'model_version': version})
                record['packages'][version] = {
                    'file': filename,
                    'manifest': (known or {}).get('manifest') or read_manifest(destination)}
                continue

            print('Syncing anomaly model', project_name, 'version', version)

            def on_progress(fraction, _completed=completed):
                base  = (_completed / total_versions) * 100
                chunk = (fraction / total_versions) * 100
                update_job_progress(round(base + chunk))

            fd, payload = tempfile.mkstemp(dir=models_dir, prefix='.payload.', suffix='.part')
            os.close(fd)
            try:
                download_package(token, project_id, version, payload, on_progress)
                install_package(payload, models_dir, filename)
            except Exception as error:
                record_job_error('failed to sync {} version {}: {}'.format(
                    project_name, version, error))
                keep_filenames.discard(filename)
                completed += 1
                update_job_progress(round((completed / total_versions) * 100))
                continue
            finally:
                if os.path.exists(payload):
                    os.unlink(payload)

            completed += 1
            update_job_progress(round((completed / total_versions) * 100))

            record = synced.setdefault(project_name, {
                'project_id': project_id, 'versions': [], 'packages': {}})
            record['versions'].append(version)
            record['packages'][version] = {
                'file': filename, 'manifest': read_manifest(destination)}

    if not synced:
        record_job_error('no anomaly packages landed — see errors above')
        return False

    prune_packages(models_dir, keep_filenames)
    save_anomaly_versions(synced)
    update_job_progress(100)
    return True
