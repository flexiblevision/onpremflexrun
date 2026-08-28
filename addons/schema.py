"""The addon descriptor contract.

An addon is a folder under addons/catalog/ containing one addon.json. Routes,
deploy, release pinning, licensing and the UI card all read that file, so adding
a service is adding a file.

Validation is strict and runs at load rather than at enable time: a descriptor
with a typo would otherwise surface as a device that accepted an enable request
and quietly deployed nothing.
"""
import re

SCHEMA = 'flexrun.addon/v1'

# container     a docker image this repo deploys (ocr, assembly, anomaly_audio)
# host_service  an apt/systemd service on the host (ftp)
# config        a stored setting with no process of its own (client mode)
# composite     several moving parts behind one toggle (timemachine)
KIND_CONTAINER = 'container'
KIND_HOST_SERVICE = 'host_service'
KIND_CONFIG = 'config'
KIND_COMPOSITE = 'composite'
KINDS = (KIND_CONTAINER, KIND_HOST_SERVICE, KIND_CONFIG, KIND_COMPOSITE)

TIER_INCLUDED = 'included'
TIER_ENTERPRISE = 'enterprise'
TIERS = (TIER_INCLUDED, TIER_ENTERPRISE)

ARCHES = ('x86', 'arm')

# "custom" means the enable flow needs its own component (a storage type, a
# master IP); the generic card then only reports state.
MANAGE_TOGGLE = 'toggle'
MANAGE_CUSTOM = 'custom'
MANAGE_MODES = (MANAGE_TOGGLE, MANAGE_CUSTOM)

GPU_MODES = ('none', 'optional', 'required')

HEALTH_TYPES = ('http', 'systemd', 'config', 'hook')

TAG_RE = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$')

# A name is a release-manifest feature key, a mongo key and a URL segment.
NAME_RE = re.compile(r'^[a-z][a-z0-9_]{1,31}$')

REQUIRED = ('schema', 'name', 'label', 'description', 'tier', 'kind', 'arches')


class AddonError(Exception):
    """Raised when a descriptor is missing, malformed, or self-contradictory."""


def validate(descriptor, source='<descriptor>'):
    """Check one descriptor and return it. Raises AddonError on any problem."""
    if not isinstance(descriptor, dict):
        raise AddonError('{}: descriptor must be a JSON object'.format(source))

    def fail(message):
        raise AddonError('{}: {}'.format(source, message))

    if descriptor.get('schema') != SCHEMA:
        fail('unsupported schema {!r}, expected {!r}'
             .format(descriptor.get('schema'), SCHEMA))

    for field in REQUIRED:
        if not descriptor.get(field):
            fail('missing {!r}'.format(field))

    name = descriptor['name']
    if not NAME_RE.match(str(name)):
        fail('name {!r} must be lowercase letters, digits and underscores, '
             '2-32 chars - it is used as a release feature key, a mongo key '
             'and a URL segment'.format(name))

    tier = descriptor['tier']
    if tier not in TIERS:
        fail('tier must be one of {}, got {!r}'.format(', '.join(TIERS), tier))

    kind = descriptor['kind']
    if kind not in KINDS:
        fail('kind must be one of {}, got {!r}'.format(', '.join(KINDS), kind))

    arches = descriptor['arches']
    if not isinstance(arches, list) or not arches:
        fail('arches must be a non-empty list')
    unknown = sorted(set(arches) - set(ARCHES))
    if unknown:
        fail('unknown arch(es): {} (known: {})'
             .format(', '.join(unknown), ', '.join(ARCHES)))

    entitlement = descriptor.get('entitlement')
    if tier == TIER_ENTERPRISE and not entitlement:
        fail('an enterprise addon must name the entitlement it is licensed by '
             '- without it the licence check has nothing to ask the cloud for '
             'and would pass by default')
    if tier == TIER_INCLUDED and entitlement:
        fail('an included addon must not declare an entitlement ({!r}) - it '
             'reads as licensed but is never checked'.format(entitlement))

    _validate_group(descriptor.get('group'), fail)
    _validate_by_kind(descriptor, kind, fail)
    _validate_health(descriptor.get('health'), kind, fail)
    _validate_ui(descriptor.get('ui'), fail)
    _validate_legacy_routes(descriptor.get('legacy_routes'), fail)

    return descriptor


def _validate_group(group, fail):
    if group is None:
        return
    if not isinstance(group, dict):
        fail('group must be an object with "key" and "label"')
    if not NAME_RE.match(str(group.get('key', ''))):
        fail('group.key {!r} must follow the same rules as a name'
             .format(group.get('key')))
    if not group.get('label'):
        fail('group.label is required - it is the heading the UI renders')


def _validate_by_kind(descriptor, kind, fail):
    if kind == KIND_CONTAINER:
        if not descriptor.get('component'):
            fail('a container addon must name its release component - that is '
                 'what the manifest pins it by')
        _validate_container(descriptor.get('container'), fail)
        return

    if kind in (KIND_HOST_SERVICE, KIND_COMPOSITE):
        if not descriptor.get('hooks'):
            fail('a {} addon must point at a hooks module - its enable and '
                 'disable cannot be expressed declaratively'.format(kind))
        return

    if kind == KIND_CONFIG:
        config = descriptor.get('config')
        if not isinstance(config, dict) or not config.get('store'):
            fail('a config addon must declare where its setting is stored')


def _validate_container(container, fail):
    if not isinstance(container, dict):
        fail('container addon is missing its "container" block')

    if not container.get('name'):
        fail('container.name is required - it is the docker container name '
             'used for teardown and for the health check')

    tag = container.get('default_tag', 'latest')
    if not TAG_RE.match(str(tag)):
        fail('container.default_tag {!r} is not a valid docker tag'.format(tag))

    gpu = container.get('gpu', 'none')
    if gpu not in GPU_MODES:
        fail('container.gpu must be one of {}, got {!r}'
             .format(', '.join(GPU_MODES), gpu))

    network = container.get('network')
    ports = container.get('ports') or []
    if network == 'host' and ports:
        fail('container declares network "host" and published ports - host '
             'networking ignores -p, so the ports would be a lie')

    for port in ports:
        if not isinstance(port, dict) or 'host' not in port or 'container' not in port:
            fail('each entry in container.ports needs "host" and "container"')
        for key in ('host', 'container'):
            value = port[key]
            if not isinstance(value, int) or isinstance(value, bool) or not 0 < value < 65536:
                fail('container.ports[].{} must be a port number, got {!r}'
                     .format(key, value))

    for volume in container.get('volumes') or []:
        if not isinstance(volume, dict):
            fail('each entry in container.volumes must be an object')
        for key in ('host', 'container'):
            if not volume.get(key):
                fail('container.volumes[] needs "{}"'.format(key))
        if not str(volume['host']).startswith('/'):
            fail('container.volumes[].host must be an absolute path, got {!r}'
                 .format(volume['host']))

    env = container.get('env') or {}
    if not isinstance(env, dict):
        fail('container.env must be an object of name -> value')
    for key, value in env.items():
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            fail('container.env[{}] must be a string or number'.format(key))


def _validate_health(health, kind, fail):
    if health is None:
        fail('every addon needs a health block - without one the device cannot '
             'tell "enabled but broken" from "disabled"')
    if not isinstance(health, dict):
        fail('health must be an object')

    health_type = health.get('type')
    if health_type not in HEALTH_TYPES:
        fail('health.type must be one of {}, got {!r}'
             .format(', '.join(HEALTH_TYPES), health_type))

    if health_type == 'http':
        port = health.get('port')
        if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
            fail('health.port must be a port number, got {!r}'.format(port))
        if not str(health.get('path', '')).startswith('/'):
            fail('health.path must start with /, got {!r}'.format(health.get('path')))

    if health_type == 'systemd' and not health.get('unit'):
        fail('health.unit is required for a systemd health check')

    if kind == KIND_CONTAINER and health_type not in ('http', 'hook'):
        fail('a container addon needs an http or hook health check, not {!r}'
             .format(health_type))


def _validate_ui(ui, fail):
    if ui is None:
        return
    if not isinstance(ui, dict):
        fail('ui must be an object')

    manage = ui.get('manage', MANAGE_TOGGLE)
    if manage not in MANAGE_MODES:
        fail('ui.manage must be one of {}, got {!r}'
             .format(', '.join(MANAGE_MODES), manage))

    order = ui.get('order', 0)
    if not isinstance(order, int) or isinstance(order, bool):
        fail('ui.order must be an integer, got {!r}'.format(order))


def _validate_legacy_routes(routes, fail):
    if routes is None:
        return
    if not isinstance(routes, dict):
        fail('legacy_routes must be an object of role -> path')
    for role, path in routes.items():
        if not str(path).startswith('/'):
            fail('legacy_routes.{} must be an absolute path, got {!r}'
                 .format(role, path))
