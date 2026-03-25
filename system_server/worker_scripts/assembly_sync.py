"""
Assembly Progress Sync Module

Handles syncing assembly progress data to the cloud using a dirty-flag
pattern. Assemblies are synced when needs_sync is True. The flag is
atomically cleared on pickup and re-set on failure to prevent duplicates.

The capture service sets needs_sync=True when assembly data changes.
This module is called from job_manager.py as part of the sync cycle.
"""

import json
import time
import requests
import os
import sys

settings_path = os.environ['HOME'] + '/flex-run'
sys.path.append(settings_path)

from pymongo import MongoClient
from bson import json_util
from redis import Redis
from rq import Queue, Retry

# Configuration
MONGODB_HOST = "172.17.0.1"
CLOUD_DOMAIN = os.environ.get('CLOUD_DOMAIN', 'https://clouddeploy.api.flexiblevision.com')
BATCH_SIZE = 50
SYNC_TIMEOUT = 60

# MongoDB connection
try:
    mongo_client = MongoClient(
        host=MONGODB_HOST,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        socketTimeoutMS=5000
    )
    assembly_collection = mongo_client["fvonprem"]["assembly_in_process"]
except Exception as e:
    print(f"Failed to connect to MongoDB for assembly sync: {e}")
    mongo_client = None
    assembly_collection = None

# Redis/RQ setup for job queue
redis_con = Redis('localhost', 6379, password=None)
job_queue = Queue('default', connection=redis_con)


def time_now_ms():
    return int(round(time.time() * 1000))


def get_unsynced_assemblies(limit=BATCH_SIZE):
    """
    Get assemblies where needs_sync is True.

    Uses find_one_and_update to atomically claim each record by setting
    needs_sync=False before returning it. This prevents duplicate pickups
    across concurrent sync cycles. On sync failure, needs_sync is set
    back to True.
    """
    if not assembly_collection:
        return []

    try:
        result = []
        for _ in range(limit):
            doc = assembly_collection.find_one_and_update(
                {'needs_sync': True},
                {'$set': {'needs_sync': False}},
                projection={'_id': 0},
                sort=[('modified_at', 1)],
                return_document=True
            )
            if not doc:
                break
            result.append(json.loads(json_util.dumps(doc)))
        return result

    except Exception as e:
        print(f"Error getting unsynced assemblies: {e}")
        return []


def mark_assemblies_synced(assembly_ids):
    """Update last_synced_at timestamp for successfully synced assemblies.

    needs_sync was already cleared atomically during pickup, so this just
    records when the sync happened for diagnostics.
    """
    if not assembly_collection or not assembly_ids:
        return

    try:
        timestamp_ms = time_now_ms()
        assembly_collection.update_many(
            {'id': {'$in': assembly_ids}},
            {'$set': {'last_synced_at': timestamp_ms}}
        )
    except Exception as e:
        print(f"Error marking assemblies synced: {e}")


def mark_assemblies_need_sync(assembly_ids):
    """Re-flag assemblies as needing sync after a failed attempt."""
    if not assembly_collection or not assembly_ids:
        return

    try:
        assembly_collection.update_many(
            {'id': {'$in': assembly_ids}},
            {'$set': {'needs_sync': True}}
        )
    except Exception as e:
        print(f"Error re-flagging assemblies for sync: {e}")


def sync_to_cloud(assemblies, access_token):
    """Push assemblies to cloud. Returns (synced_ids, failed_ids)."""
    if not assemblies:
        return [], []

    try:
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        url = f'{CLOUD_DOMAIN}/api/assembly/progress/sync'

        res = requests.post(
            url,
            headers=headers,
            json={'assemblies': assemblies},
            timeout=SYNC_TIMEOUT
        )

        if res.status_code == 200:
            result = res.json()
            return result.get('synced_ids', []), result.get('failed_ids', [])
        else:
            print(f"Cloud sync failed: HTTP {res.status_code}")
            return [], [a['id'] for a in assemblies]

    except requests.exceptions.Timeout:
        print("Cloud sync timeout")
        return [], [a['id'] for a in assemblies]
    except Exception as e:
        print(f"Cloud sync error: {e}")
        return [], [a['id'] for a in assemblies]


def assembly_sync_job(assemblies, access_token):
    """
    Job-compatible function for syncing a batch of assemblies.

    Called via job_queue.enqueue() with retry logic.
    needs_sync was already cleared during pickup. On failure, re-flag
    so the next cycle picks them up again.

    Returns:
        True on success, False on failure (triggers retry)
    """
    if not assemblies:
        return True

    try:
        synced_ids, failed_ids = sync_to_cloud(assemblies, access_token)

        if synced_ids:
            mark_assemblies_synced(synced_ids)
            print(f"Assembly sync job: {len(synced_ids)} synced")

        if failed_ids:
            mark_assemblies_need_sync(failed_ids)

        return len(failed_ids) == 0

    except Exception as e:
        # Re-flag all assemblies so they're retried
        all_ids = [a['id'] for a in assemblies if 'id' in a]
        mark_assemblies_need_sync(all_ids)
        print(f"Assembly sync job error: {e}")
        return False


def push_assembly_progress_to_cloud(access_token):
    """
    Main sync function - gets unsynced assemblies and pushes to cloud.

    Called by job_manager as part of the sync cycle.

    Args:
        access_token: Bearer token for cloud API

    Returns:
        Dict with sync results
    """
    result = {
        'success': True,
        'synced_count': 0,
        'failed_count': 0,
        'error': None
    }

    try:
        assemblies = get_unsynced_assemblies(limit=BATCH_SIZE)

        if not assemblies:
            return result

        print(f"Syncing {len(assemblies)} assemblies to cloud...")

        synced_ids, failed_ids = sync_to_cloud(assemblies, access_token)

        if synced_ids:
            mark_assemblies_synced(synced_ids)

        if failed_ids:
            mark_assemblies_need_sync(failed_ids)

        result['synced_count'] = len(synced_ids)
        result['failed_count'] = len(failed_ids)

        if failed_ids:
            result['success'] = False
            result['error'] = f"Failed to sync {len(failed_ids)} assemblies"

    except Exception as e:
        # Re-flag all so they're retried next cycle
        all_ids = [a['id'] for a in assemblies if 'id' in a]
        mark_assemblies_need_sync(all_ids)
        result['success'] = False
        result['error'] = str(e)
        print(f"Error in push_assembly_progress_to_cloud: {e}")

    return result


def push_assembly_progress(access_token):
    """
    Sync assembly progress to cloud via job queue.

    Uses dirty-flag pattern: only syncs assemblies where needs_sync
    is True. Flag is atomically cleared on pickup, re-set on failure.
    """
    from worker_scripts.job_manager import insert_job

    try:
        assemblies = get_unsynced_assemblies(limit=BATCH_SIZE)

        if not assemblies:
            return True

        print(f'#Assembly progress: {len(assemblies)}')

        # Enqueue in batches (similar to analytics pattern)
        for i in range(0, len(assemblies), BATCH_SIZE):
            batch = assemblies[i:i + BATCH_SIZE]
            if not batch:
                break

            j_push = job_queue.enqueue(
                assembly_sync_job,
                batch,
                access_token,
                job_timeout=300,
                result_ttl=3600,
                retry=Retry(max=5, interval=60)
            )
            if j_push:
                insert_job(j_push.id, f'Syncing_{len(batch)}_assemblies_with_cloud')

        return True

    except Exception as e:
        print(f"Assembly sync error: {e}")
        return False
