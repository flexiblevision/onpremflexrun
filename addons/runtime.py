"""Deploy, tear down, and probe an addon from its descriptor.

Replaces helpers/install_{ocr,assembly,audio}.sh, which had drifted into three
scripts doing the same job three ways.

Image references go through resolve_reference(): today repo:tag, as the install
scripts did, but the seam takes a pinned digest so the upgrade path can hand in
what release/manifest.py:pinned_reference() produces.
"""
import os
import subprocess

import requests

from . import registry, schema

REGISTRY_NAMESPACE = 'fvonprem'
DOCKER = 'docker'

DEFAULT_PROBE_HOST = '172.17.0.1'

DEPLOY_TIMEOUT = 600
PULL_TIMEOUT = 900


class DeployError(Exception):
    """Raised when a deploy or teardown cannot be carried out."""


def system_arch(uname=None):
    machine = uname or os.uname().machine
    if machine == 'x86_64':
        return 'x86'
    if machine == 'aarch64':
        return 'arm'
    return machine


def repository(arch, component):
    return '{}/{}-{}'.format(REGISTRY_NAMESPACE, arch, component)


def resolve_reference(addon, arch, reference=None):
    """The image to run: a caller-supplied pinned digest, or repo:tag."""
    if reference:
        return reference

    container = addon['container']
    tag = os.environ.get('IMAGE_TAG') or container.get('default_tag', 'latest')
    return '{}:{}'.format(repository(arch, addon['component']), tag)


def build_run_argv(addon, image, gpu=True):
    """The full `docker run` argv. Built as argv, never a shell string."""
    container = addon['container']
    argv = [DOCKER, 'run', '-d', '--name', container['name']]

    restart = container.get('restart')
    if restart:
        argv += ['--restart', restart]

    if container.get('network'):
        argv += ['--network', container['network']]

    if gpu and container.get('gpu', 'none') != 'none':
        argv += ['--gpus', container.get('gpu_device', 'device=0')]

    for port in container.get('ports') or []:
        argv += ['-p', '{}:{}'.format(port['host'], port['container'])]

    for key in sorted(container.get('env') or {}):
        argv += ['-e', '{}={}'.format(key, container['env'][key])]

    for volume in container.get('volumes') or []:
        mount = '{}:{}'.format(volume['host'], volume['container'])
        if volume.get('mode'):
            mount += ':' + volume['mode']
        argv += ['-v', mount]

    for key in sorted(container.get('log_opts') or {}):
        argv += ['--log-opt', '{}={}'.format(key, container['log_opts'][key])]

    argv += container.get('args') or []
    argv.append(image)
    argv += container.get('command') or []
    return argv


def _run(argv, timeout=DEPLOY_TIMEOUT):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def pull(image):
    # Tolerated so a device with no registry access can still re-create a
    # container from a local image; a genuinely missing image fails at run.
    result = _run([DOCKER, 'pull', image], timeout=PULL_TIMEOUT)
    if result.returncode != 0:
        print('addon pull failed for {}, falling back to a local image: {}'
              .format(image, (result.stderr or '').strip()[-300:]))
        return False
    return True


def remove_container(name):
    _run([DOCKER, 'stop', name])
    _run([DOCKER, 'rm', name])


def is_running(name):
    result = _run([DOCKER, 'inspect', '-f', '{{.State.Running}}', name])
    return result.returncode == 0 and result.stdout.strip() == 'true'


def current_image(name):
    result = _run([DOCKER, 'inspect', '-f', '{{.Config.Image}}', name])
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _ensure_volumes(addon):
    # Not fatal: docker creates a missing bind-mount source itself, as root.
    for volume in addon['container'].get('volumes') or []:
        if not volume.get('create'):
            continue
        try:
            os.makedirs(volume['host'], exist_ok=True)
        except OSError as error:
            print('could not pre-create {} for {}: {}'
                  .format(volume['host'], addon['name'], error))


def deploy(name, arch=None, reference=None):
    """Bring an addon up, replacing whatever is there. Returns the image."""
    addon = registry.get(name)
    if addon['kind'] != schema.KIND_CONTAINER:
        hooks = registry.hooks_module(addon)
        if hooks is None or not hasattr(hooks, 'enable'):
            raise DeployError(
                'addon {!r} is {} and has no enable hook'
                .format(name, addon['kind']))
        return hooks.enable(addon)

    arch = arch or system_arch()
    if arch not in addon['arches']:
        raise DeployError(
            '{} is not available for {} (built for: {})'
            .format(name, arch, ', '.join(addon['arches'])))

    image = resolve_reference(addon, arch, reference)
    container_name = addon['container']['name']

    _ensure_volumes(addon)
    pull(image)
    remove_container(container_name)

    wants_gpu = addon['container'].get('gpu', 'none')
    result = _run(build_run_argv(addon, image, gpu=wants_gpu != 'none'))

    # "optional" means the image runs on CPU too; without the retry a device
    # with no nvidia runtime could not enable the addon at all.
    if result.returncode != 0 and wants_gpu == 'optional':
        print('{} failed to start with a GPU, retrying on CPU: {}'
              .format(container_name, (result.stderr or '').strip()[-300:]))
        remove_container(container_name)
        result = _run(build_run_argv(addon, image, gpu=False))

    if result.returncode != 0:
        raise DeployError(
            'could not start {}: {}'
            .format(container_name, (result.stderr or '').strip()[-500:]))

    return image


def teardown(name):
    """Take an addon down, leaving its data volumes alone."""
    addon = registry.get(name)

    if addon['kind'] != schema.KIND_CONTAINER:
        hooks = registry.hooks_module(addon)
        if hooks is None or not hasattr(hooks, 'disable'):
            raise DeployError(
                'addon {!r} is {} and has no disable hook'
                .format(name, addon['kind']))
        return hooks.disable(addon)

    remove_container(addon['container']['name'])
    return True


def health(name):
    """Is the addon answering right now? Separate from whether it is enabled."""
    addon = registry.get(name)
    check = addon.get('health') or {}
    check_type = check.get('type')

    if check_type == 'http':
        url = 'http://{}:{}{}'.format(
            check.get('host', DEFAULT_PROBE_HOST),
            check['port'],
            check.get('path', '/'))
        try:
            response = requests.get(url, timeout=check.get('timeout', 2))
            return response.status_code == 200
        except Exception as error:
            print('{} health probe failed: {}'.format(name, error))
            return False

    if check_type == 'systemd':
        result = _run(['systemctl', 'is-active', '--quiet', check['unit']])
        return result.returncode == 0

    if check_type in ('hook', 'config'):
        hooks = registry.hooks_module(addon)
        if hooks is None or not hasattr(hooks, 'status'):
            return False
        return bool(hooks.status(addon))

    return False
