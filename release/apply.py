"""Turn a verified manifest into something the deploy scripts can apply.

The deploy scripts take version tags, because that is what they have always
taken and every device in the field runs them. Rather than rewrite that, this
writes the digests beside the run so pinned_ref() in deploy_common.sh can
prefer them - the scripts keep their shape, and the pull becomes exact.

Nothing here verifies anything. Call release/verify.py first and pass the
parsed manifest it returned; a manifest that reaches this module is one the
device has already decided to trust.
"""
import os
import tempfile

from . import manifest as manifest_mod

PLAN_ENV = 'FLEXRUN_PLAN'

# The order upgrade_system.sh takes its arguments, which is NOT the order the
# script it dispatches to reads them: upgrade_system.sh inserts the arch itself
# at position 4. Encoding the inner script's order here instead would shift
# every version by one and upgrade the backend to the frontend's version.
# Kept in step with upgrade_runner.VERSION_ARGS by a test.
ARGUMENT_ORDER = ('backend', 'frontend', 'prediction', 'predictlite',
                  'vision', 'nodecreator', 'visiontools')

# What a component is called to the deploy scripts, when that differs.
UP_TO_DATE = 'True'


class ApplyError(Exception):
    pass


def plan_lines(parsed, arch, current=None):
    """'<component> <version> <repo>@sha256:...' per component, sorted.

    Every component in the release appears, including ones with no positional
    argument slot - which is the point: vernemq is foundational and used to be
    upgraded by a hardcoded block outside the scheme.
    """
    components = manifest_mod.components_for(parsed, arch)
    current = current or {}
    lines = []
    for name in sorted(components):
        tag = components[name]['tag']
        version = UP_TO_DATE if current.get(name) == tag else tag
        lines.append('{} {} {}'.format(
            name, version, manifest_mod.pinned_reference(parsed, arch, name)))
    return lines


def write_plan(parsed, arch, path, current=None):
    """Write the plan file the deploy scripts read. Returns the path.

    Written whole then moved into place: a deploy script reading a half-written
    file would silently fall back to tags for whatever had not been flushed,
    which is the failure this whole change exists to remove.
    """
    body = '\n'.join(plan_lines(parsed, arch, current=current)) + '\n'
    directory = os.path.dirname(os.path.abspath(path)) or '.'
    if not os.path.isdir(directory):
        os.makedirs(directory)

    handle = tempfile.NamedTemporaryFile(
        'w', dir=directory, prefix='.plan-', delete=False)
    try:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.chmod(handle.name, 0o644)
        os.replace(handle.name, path)
    except Exception:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
    return path


def versions_for(parsed, arch, current=None):
    """The positional version arguments upgrade_system.sh expects.

    'True' means "already at this version, skip it" to those scripts. Passing
    the tag for something unchanged would tear a working container down and
    rebuild it for nothing, which on a line is downtime with no upgrade.
    """
    components = manifest_mod.components_for(parsed, arch)
    current = current or {}

    versions = []
    for name in ARGUMENT_ORDER:
        entry = components.get(name)
        if entry is None:
            # Not in this release for this arch - visiontools on arm, say.
            versions.append(UP_TO_DATE)
            continue
        tag = entry['tag']
        versions.append(UP_TO_DATE if current.get(name) == tag else tag)
    return versions


def plan(parsed, arch, current=None, plan_path=None):
    """Everything the runner needs: the argument list and the digest file.

    Returned together because they have to agree - versions decide which
    containers are touched, digests decide what bytes they get, and a mismatch
    between them is an upgrade that pulls one thing and runs another.
    """
    if parsed.get('arch') and parsed['arch'] != arch:
        raise ApplyError(
            'manifest is for {} but this device is {}'
            .format(parsed['arch'], arch))

    versions = versions_for(parsed, arch, current=current)
    path = None
    if plan_path:
        path = write_plan(parsed, arch, plan_path, current=current)
    return {
        'versions': versions,
        'plan_path': path,
        'counter': parsed.get('counter'),
        'release': parsed.get('release'),
        'changing': [n for n in ARGUMENT_ORDER
                     if versions[ARGUMENT_ORDER.index(n)] != UP_TO_DATE],
    }
