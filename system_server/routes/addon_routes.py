"""One generic route surface for every addon.

Replaces audio_routes.py and the toggle/status pairs embedded in
assembly_routes.py and timemachine_routes.py - the same forty lines three times
over, each with its own Redis connection and hardcoded port.

    GET  /addons          every addon, with intent and health reported apart
    GET  /addons/<name>   one
    PUT  /addons/<name>   {"state": true|false}

The old paths are registered too, generated from each descriptor's
legacy_routes, because captureui is upgraded independently of flex-run.
"""
from flask import g, request
from flask_restx import Resource
from redis import Redis
from rq import Queue, Retry

import auth
from addons import entitlements, jobs, registry, runtime, schema, state
from worker_scripts.job_manager import insert_job

redis_con = Redis('localhost', 6379, password=None)
job_queue = Queue('default', connection=redis_con)

ENABLE_TIMEOUT = 900


def _access_token():
    return request.headers.get('Access-Token')


def _claims():
    # Set by auth.requires_auth after it verifies the token against the JWKS,
    # so these are signed. Absent on the unauthenticated read routes.
    return getattr(g, 'current_user', None)


def _summary(addon, records):
    name = addon['name']
    record = records.get(name) or {}
    grant = entitlements.check(addon, claims=_claims())

    return {
        'name': name,
        'label': addon['label'],
        'description': addon['description'],
        'group': addon.get('group'),
        'tier': addon['tier'],
        'kind': addon['kind'],
        'arches': addon['arches'],
        'ui': addon.get('ui') or {},
        'enabled': bool(record.get('enabled')),
        'healthy': runtime.health(name),
        'image': record.get('image'),
        'last_error': record.get('last_error'),
        'entitlement': grant.as_dict(),
        'legacy_routes': addon.get('legacy_routes') or {},
    }


def _enqueue_enable(name, access_token=None):
    job = job_queue.enqueue(
        jobs.enable_addon,
        name,
        access_token=access_token,
        job_timeout=ENABLE_TIMEOUT,
        result_ttl=3600,
        retry=Retry(max=5, interval=60),
    )
    insert_job(job.id, 'installing and deploying {}'.format(name))
    return job


class AddonList(Resource):
    def get(self):
        records = state.all_records()
        addons = registry.for_arch(runtime.system_arch())
        return sorted(
            (_summary(addon, records) for addon in addons.values()),
            key=lambda entry: (entry['ui'].get('order', 0), entry['name']))


class Addon(Resource):
    def get(self, name):
        try:
            addon = registry.get(name)
        except registry.RegistryError as error:
            return {'error': str(error)}, 404
        return _summary(addon, state.all_records())

    @auth.requires_auth
    def put(self, name):
        try:
            addon = registry.get(name)
        except registry.RegistryError as error:
            return {'error': str(error)}, 404

        body = request.json or {}
        if 'state' not in body:
            return {'error': 'state key not found'}, 400

        if not body['state']:
            jobs.disable_addon(name)
            return {'name': name, 'enabled': False, 'status': 'disabled'}, 200

        grant = entitlements.check(addon, _access_token(), _claims())
        if grant.blocking:
            return {'error': '{} is not licensed on this device'.format(addon['label']),
                    'entitlement': grant.as_dict()}, 403

        job = _enqueue_enable(name, _access_token())
        return {'name': name, 'enabled': True, 'status': 'enabling',
                'job': job.id, 'entitlement': grant.as_dict()}, 200


def _legacy_manage(name, label):
    # Body and status codes preserved exactly: an older captureui checks
    # neither carefully, and changing either would look like a hang.
    class LegacyManage(Resource):
        @auth.requires_auth
        def put(self):
            body = request.json or {}
            if 'state' not in body:
                return 'state key not found', 404

            if body['state']:
                _enqueue_enable(name, _access_token())
                return 'enabling...', 200

            jobs.disable_addon(name)
            return 'disabled', 200

    LegacyManage.__name__ = 'LegacyManage{}'.format(label)
    return LegacyManage


def _legacy_status(name, label):
    # Still a live probe rather than the recorded state, so an older UI sees
    # exactly what it saw before. The new surface reports both.
    class LegacyStatus(Resource):
        def get(self):
            return runtime.health(name)

    LegacyStatus.__name__ = 'LegacyStatus{}'.format(label)
    return LegacyStatus


def register_routes(api):
    api.add_resource(AddonList, '/addons')
    api.add_resource(Addon, '/addons/<string:name>')

    for name, addon in sorted(registry.load().items()):
        # Only addons this surface actually manages. The rest list their paths
        # for reference but are served by their own module or by capdev.
        if (addon.get('ui') or {}).get('manage') != schema.MANAGE_TOGGLE:
            continue

        legacy = addon.get('legacy_routes') or {}
        label = ''.join(part.title() for part in name.split('_'))

        if legacy.get('manage'):
            api.add_resource(_legacy_manage(name, label), legacy['manage'])
        if legacy.get('status'):
            api.add_resource(_legacy_status(name, label), legacy['status'])
