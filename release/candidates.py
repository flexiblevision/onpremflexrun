"""Find the newest CI-built version of each component.

This automates the *lookup*, not the decision. It writes release/components.json
and shows you what changed; committing that diff is still the promote, with an
author and a revert. Nobody has to know or type a version number.

Two rules it will not bend:

  Only CI-built images are offered. A tag with no
  org.opencontainers.image.revision label was not built by CI on master, so it
  is skipped - a hand-pushed image from a laptop can never be auto-suggested
  into a release.

  It never silently goes backwards. If the newest candidate sorts below what is
  pinned today, that is reported and the existing pin is kept, because a
  downgrade the fleet takes by accident is exactly the failure this pipeline
  exists to prevent.
"""
import re

from . import manifest as manifest_mod
from . import provenance as provenance_mod

# Only dotted numbers. This deliberately excludes the :<sha> tags CI also
# pushes, and channel names like "dev"/"prod"/"latest" - none of which name a
# version, and one of which moves.
VERSION_TAG_RE = re.compile(r'^\d+(\.\d+)*$')

# How far down the sorted tag list to look for a labelled image before giving
# up. Each check costs two registry calls, and if the newest few are all
# unlabelled the component simply has no CI build yet.
MAX_PROBE = 12


class CandidateError(Exception):
    pass


def version_key(tag):
    """Sort key for a dotted numeric tag.

    Compared component by component as integers, so 1.10 sorts above 1.9 - which
    a string sort gets backwards, and which is exactly the case that would pin an
    older image while looking correct.
    """
    return tuple(int(part) for part in tag.split('.'))


def version_tags(tags):
    """Dotted-numeric tags only, newest first."""
    usable = [t for t in tags if VERSION_TAG_RE.match(t)]
    return sorted(usable, key=version_key, reverse=True)


def newest_built(repository, list_tags, fetch_labels, max_probe=MAX_PROBE):
    """Newest version tag on this repository that CI built.

    Returns (tag, revision) or (None, reason).
    """
    try:
        tags = list_tags(repository)
    except Exception as exc:
        return None, str(exc).splitlines()[0][:80]

    ordered = version_tags(tags)
    if not ordered:
        return None, 'no version-shaped tags'

    for tag in ordered[:max_probe]:
        labels = fetch_labels(repository, tag) or {}
        ours = {k: v for k, v in labels.items()
                if k not in provenance_mod.INHERITED}
        status, revision = provenance_mod.classify(ours)
        if status == provenance_mod.OK:
            return tag, revision

    return None, 'no CI-built tag in the newest {}'.format(
        min(len(ordered), max_probe))


def survey(current, list_tags, fetch_labels, arches=manifest_mod.ARCHES):
    """What each component could move to. Pure reporting - changes nothing.

    `current` is the components mapping as it stands today. Returns a list of
    records, one per component per arch, in a stable order.
    """
    records = []
    for arch in sorted(arches):
        for component in sorted(current.get(arch) or {}):
            pinned = current[arch][component]
            repo = manifest_mod.repository(arch, component)
            tag, detail = newest_built(repo, list_tags, fetch_labels)

            if tag is None:
                state, proposed = 'none', pinned
            elif tag == pinned:
                state, proposed = 'same', pinned
            elif not VERSION_TAG_RE.match(pinned):
                # A pin that is not a version at all - vernemq's "dev" channel.
                # There is no ordering to reason about, so leave it alone rather
                # than guess that a numbered tag is an improvement.
                state, proposed = 'channel', pinned
                detail = 'pinned to a channel, not a version - not touched'
            elif version_key(tag) > version_key(pinned):
                state, proposed = 'upgrade', tag
            else:
                # Newest CI build is older than the pin. Keep the pin.
                state, proposed = 'behind', pinned

            records.append({
                'arch': arch,
                'component': component,
                'repository': repo,
                'pinned': pinned,
                'candidate': tag,
                'proposed': proposed,
                'state': state,
                'detail': detail if state in ('none', 'channel') else None,
            })
    return records


def apply(current, records):
    """A new components mapping with the proposed tags. Does not mutate."""
    updated = {arch: dict(comps) for arch, comps in current.items()}
    for record in records:
        updated[record['arch']][record['component']] = record['proposed']
    return updated


def describe(records, stream):
    """Print the survey. Returns True if anything would change."""
    changed = [r for r in records if r['proposed'] != r['pinned']]

    for record in records:
        if record['state'] == 'upgrade':
            line = '{} -> {}'.format(record['pinned'], record['proposed'])
            mark = 'move'
        elif record['state'] == 'same':
            line = '{} (already newest)'.format(record['pinned'])
            mark = 'ok'
        elif record['state'] == 'behind':
            line = '{} kept - newest CI build is {}, which is older'.format(
                record['pinned'], record['candidate'])
            mark = 'warn'
        elif record['state'] == 'channel':
            line = '{} kept - {}'.format(record['pinned'], record['detail'])
            mark = 'ok'
        else:
            line = '{} kept - {}'.format(record['pinned'], record['detail'])
            mark = 'warn'
        stream.write('  [{:4}] {:<4} {:<12} {}\n'.format(
            mark, record['arch'], record['component'], line))

    if not changed:
        stream.write('\nNothing to move: every component is already pinned to '
                     'the newest CI build.\n')
        return False

    stream.write('\n{} component(s) would move. Review the diff and commit it - '
                 'that commit\nis the promote.\n'.format(len(changed)))
    return True
