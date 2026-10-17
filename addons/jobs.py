"""The rq entry points for enabling and disabling an addon.

Module-level because rq serialises a function by import path. Keep the names
stable: renaming one strands any job already queued under the old path.
"""
from . import entitlements, registry, runtime, state


def enable_addon(name, by=None, access_token=None, reference=None):
    addon = registry.get(name)

    grant = entitlements.check(addon, access_token)
    if grant.blocking:
        state.mark_failed(name, 'not licensed: {}'.format(grant.reason))
        raise PermissionError(
            '{} is not licensed on this device ({})'.format(name, grant.reason))
    if not grant.allowed:
        print('addon {} enabled without a confirmed licence ({}) - advisory '
              'until entitlements.ENFORCED'.format(name, grant.reason))

    try:
        image = runtime.deploy(name, reference=reference)
    except Exception as error:
        state.mark_failed(name, error)
        raise

    state.mark_enabled(name, image=image, by=by)
    return image


def disable_addon(name, by=None):
    try:
        runtime.teardown(name)
    except Exception as error:
        state.mark_failed(name, error)
        raise

    state.mark_disabled(name, by=by)
    return True
