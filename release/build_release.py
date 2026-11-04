"""Assemble, and optionally sign, a release.

Everything the release is stamped with is derived, so cutting a release takes no
decisions at the moment it happens:

  version   MAJOR.MINOR, e.g. 1.0 ... 1.9. The major and the counter that
            starts the series are checked in (release/VERSION); the minor is
            counter - first, so it increments once per release and cannot be
            forgotten or reused. Past .9 the tool refuses and asks for a new
            major series, because 1.10 sorts below 1.9 everywhere.
  counter   the per-arch monotonic integer, one more than the highest release
            tag. This is what a device compares - the version string is for
            people.
  digests   resolved from the registry, so this does not care where or how any
            image was built.
  notAfter  created + a window. Reported by the device, not enforced - see
            release/verify.py.

Git tags are the state behind BUILD. A checked-in counter file is the obvious
alternative and is worse: a bad merge can move it backwards, which silently
disarms the anti-rollback check. Tags can be protected and a deletion is
visible.

The component set can come from two places:

  --from-stable   what the existing latest_stable_version endpoint currently
                  serves. This is how release 1 gets seeded - it captures what
                  the fleet is actually running today rather than a guess.
  --components    a checked-in name->tag map. This is the steady state, because
                  changing it is a reviewable commit with an author and a
                  revert, which is the whole point of moving the decision out
                  of a mutable cloud value.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

from . import manifest as manifest_mod
from . import registry as registry_mod

# release/<arch>/<n>. Counters are per architecture: x86 and arm ship on their
# own cadence, and a shared sequence would make an arm device look behind
# because an x86 release consumed a number it will never see.
RELEASE_TAG_RE = re.compile(r'^release/([a-z0-9]+)/(\d+)$')
REMOTE_TAG_RE = re.compile(r'^[0-9a-f]{40}\s+refs/tags/(release/[a-z0-9]+/\d+)$')
# "<major> <first-counter>": the major series, and the counter whose release is
# .0 in it. The minor is then counter - first, so it increments once per release
# without anyone editing a file per cut.
#
# Two numbers rather than one because the counter must stay monotonic for
# anti-rollback and cannot restart at 0 for a new major series. Moving to 2.0 is
# writing "2 <the next counter>".
VERSION_RE = re.compile(r'^(\d+)\s+(\d+)$')
# Ten was too few once beta and stable share one counter: three beta
# iterations before each stable release exhausts a series in a handful of
# releases, and you end up starting 2.0 for reasons unrelated to the software.
# Two digits is enough that the series ends when someone decides it should.
MAX_MINOR = 99

DEFAULT_REMOTE = 'origin'

DEFAULT_VALID_DAYS = 90


class BuildError(Exception):
    pass


# --- derivations (pure, so they are testable without git or a network) -------

def parse_version_file(text):
    """'1 4' -> (1, 4): major series 1, whose .0 release is counter 4."""
    stripped = (text or '').strip()
    match = VERSION_RE.match(stripped)
    if not match:
        raise BuildError(
            'expected "<major> <first-counter>" in the version file, got {!r}. '
            'The old MAJOR.MINOR form is gone: the minor is now derived from '
            'the counter so it cannot be forgotten or reused.'.format(stripped))
    return int(match.group(1)), int(match.group(2))


def next_build(existing_tags, arch):
    """One more than the highest release tag for this arch. Never reuses.

    Anything that is not release/<arch>/<int> is ignored rather than guessed at,
    so an unrelated tag - or another architecture's - cannot perturb this
    sequence.
    """
    if not arch:
        raise BuildError('next_build needs an arch: counters are per architecture')
    highest = 0
    for tag in existing_tags or ():
        match = RELEASE_TAG_RE.match(str(tag).strip())
        if match and match.group(1) == arch:
            highest = max(highest, int(match.group(2)))
    return highest + 1


def release_version(major, first_counter, build):
    """'1.0', '1.1' ... '1.99'. Refuses to go past MAX_MINOR.

    Past MAX_MINOR the next release is a new major series, which is a decision
    to make rather than a number to roll over.
    """
    minor = build - first_counter
    if minor < 0:
        raise BuildError(
            'counter {} is below the first counter {} of major series {} - '
            'the version file names a series that has not started yet'
            .format(build, first_counter, major))
    if minor > MAX_MINOR:
        raise BuildError(
            'major series {} is exhausted: counter {} would be {}.{}. Start the '
            'next series by writing "{} {}" to release/VERSION.'
            .format(major, build, major, minor, major + 1, build))
    return '{}.{}'.format(major, minor)


def git_release_tags(run=None):
    runner = run or (lambda argv: subprocess.run(
        argv, capture_output=True, text=True, check=False))
    result = runner(['git', 'tag', '--list', 'release/*'])
    if result.returncode != 0:
        raise BuildError('could not list release tags: {}'.format(
            (result.stderr or '').strip()))
    return [line.strip() for line in (result.stdout or '').splitlines() if line.strip()]


def git_head(run=None):
    runner = run or (lambda argv: subprocess.run(
        argv, capture_output=True, text=True, check=False))
    result = runner(['git', 'rev-parse', 'HEAD'])
    if result.returncode != 0:
        raise BuildError('could not read HEAD: {}'.format((result.stderr or '').strip()))
    return (result.stdout or '').strip()


def remote_release_tags(run=None, remote=DEFAULT_REMOTE):
    """Release tags as the remote sees them.

    The remote is the authority, not the local tag list. A local list is stale
    the moment anyone else cuts a release, and reading a stale list is exactly
    how two releases end up sharing a counter - which silently disarms the
    anti-rollback check on every device that has already seen that number.
    """
    runner = run or (lambda argv: subprocess.run(
        argv, capture_output=True, text=True, check=False))
    result = runner(['git', 'ls-remote', '--tags', remote, 'refs/tags/release/*'])
    if result.returncode != 0:
        raise BuildError(
            'could not read release tags from {}: {}. The counter comes from '
            'the remote, so a release cannot be cut without reaching it.'
            .format(remote, (result.stderr or '').strip()))

    tags = []
    for line in (result.stdout or '').splitlines():
        match = REMOTE_TAG_RE.match(line.strip())
        if match:
            tags.append(match.group(1))
    return tags


def reserve_build(build_no, commit, arch, run=None, remote=DEFAULT_REMOTE,
                  message=None):
    """Claim a build number on the remote, before anything is signed.

    Pushed first rather than last, and that ordering is the point. Two people
    cutting at once means one push is rejected instead of two signed releases
    quietly sharing a counter.

    The tag is annotated, and that is load-bearing rather than cosmetic. A
    lightweight tag is just the commit sha, so when two cutters are on the same
    HEAD - the normal case - the second push is a no-op that git reports as
    "Everything up-to-date" and exits 0. It claims nothing. An annotated tag is
    its own object, so the two cutters produce different objects and the second
    push is refused with "already exists", which is the behaviour this needs.
    --force-with-lease does not help here: git short-circuits before it ever
    attempts a ref update it believes is unchanged.

    The cost of reserving up front is that aborting after this point burns the
    number. That is the right way round: devices compare counters as integers,
    so a gap in the sequence is invisible to them, while a reused counter is not.
    """
    runner = run or (lambda argv: subprocess.run(
        argv, capture_output=True, text=True, check=False))
    name = 'release/{}/{}'.format(arch, build_no)
    ref = 'refs/tags/' + name
    message = message or 'release build {} ({})'.format(build_no, arch)

    # -f because a previous cut that failed at the push may have left this tag
    # behind locally. The remote is the authority on what is taken, so a local
    # leftover must not block a retry.
    created = runner(['git', 'tag', '-a', '-f', '-m', message, name, commit])
    if created.returncode != 0:
        raise BuildError('could not create tag {}: {}'.format(
            name, (created.stderr or '').strip()))

    result = runner(['git', 'push', remote, ref])
    if result.returncode != 0:
        # Leaving it behind would have the local repo claiming a number the
        # remote never granted, and the next cut would skip over it.
        runner(['git', 'tag', '-d', name])
        raise BuildError(
            'could not reserve {} on {}: {}\n'
            'If it was rejected because the tag already exists, someone else '
            'took that number - re-run and this will pick up the next one.'
            .format(name, remote, (result.stderr or '').strip()))
    return ref


# --- component sets ---------------------------------------------------------

def _coerce_tags(value, label):
    """Stringify a tag map, one level or per-arch. Both shapes occur: the
    architectures are often on different version streams."""
    if not isinstance(value, dict) or not value:
        raise BuildError('{} must be a non-empty JSON object'.format(label))

    if all(isinstance(inner, dict) for inner in value.values()):
        return {str(arch): {str(k): str(v) for k, v in inner.items()}
                for arch, inner in value.items()}

    if any(isinstance(inner, dict) for inner in value.values()):
        raise BuildError(
            '{} mixes per-arch and flat entries - use either {{"x86": {{...}}, '
            '"arm": {{...}}}} or a single {{name: tag}} map'.format(label))

    return {str(k): str(v) for k, v in value.items()}


def components_from_file(text):
    """A checked-in tag map. JSON so there is no YAML dependency."""
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise BuildError('component file is not valid JSON: {}'.format(exc))
    if not isinstance(parsed, dict):
        raise BuildError('component file must be a JSON object of name -> tag')

    raw_tags = parsed.get('components', parsed)
    if not isinstance(raw_tags, dict) or not raw_tags:
        raise BuildError('component file has no components')

    raw_features = parsed.get('features', {})
    if not isinstance(raw_features, dict):
        raise BuildError('features must be a JSON object of name -> tag')

    tags = _coerce_tags(raw_tags, 'components')
    features = _coerce_tags(raw_features, 'features') if raw_features else {}
    return tags, features


def components_from_stable(fetch, components=manifest_mod.FOUNDATIONAL, arch='x86'):
    """Seed from the endpoint the fleet already uses as its source of truth.

    A one-time import, not an ongoing dependency: it records what devices are
    running today so release 1 starts from reality. If the two architectures
    disagree the release is ambiguous, so that is an error rather than a
    silent pick.
    """
    tags = {}
    for component in components:
        value = fetch(component, arch)
        if not value or not str(value).strip():
            raise BuildError(
                'the stable-version endpoint returned nothing for {!r} - a '
                'release cannot pin a component whose version is unknown'
                .format(component))
        tags[component] = str(value).strip()
    return tags


# --- assembly ---------------------------------------------------------------

def build(version_text, existing_tags, flexrun_commit, tags, resolver, now,
          arch='x86', features=None, valid_days=DEFAULT_VALID_DAYS, notes=None,
          major_override=None):
    """major_override starts a new series at this release: it becomes <N>.0.

    Decided here, at the cut, because the version string is inside the bytes
    that get signed. Renaming a release at promote time would mean re-signing
    it, and then the thing shipped to stable would not be the thing tested on
    beta - which is the property promoting exists to preserve.
    """
    major, first = parse_version_file(version_text)
    build_no = next_build(existing_tags, arch)

    if major_override is not None:
        if major_override <= major:
            raise BuildError(
                'major {} is not ahead of the current series {} - a major jump '
                'goes forwards'.format(major_override, major))
        major, first = major_override, build_no

    version = release_version(major, first, build_no)

    document = manifest_mod.build_manifest(
        release=version,
        counter=build_no,
        tags=tags,
        features=features,
        flexrun_commit=flexrun_commit,
        resolver=resolver,
        now=now,
        valid_days=valid_days,
        notes=notes,
        arches=(arch,),
    )
    return document, build_no


def diff_summary(previous, current, arch='x86'):
    """Which components moved between two releases.

    This is what release notes are made of, and it is derivable rather than
    written by hand: one changed digest means one changed component.
    """
    if not previous:
        return {'changed': sorted(manifest_mod.components_for(current, arch)),
                'unchanged': [], 'added': [], 'removed': []}

    before = manifest_mod.components_for(previous, arch)
    after = manifest_mod.components_for(current, arch)

    changed, unchanged = [], []
    for name in sorted(set(before) & set(after)):
        if before[name]['digest'] != after[name]['digest']:
            changed.append(name)
        else:
            unchanged.append(name)
    return {
        'changed': changed,
        'unchanged': unchanged,
        'added': sorted(set(after) - set(before)),
        'removed': sorted(set(before) - set(after)),
    }


# --- cli --------------------------------------------------------------------

def _stable_fetcher(base, ref):
    import requests

    def fetch(component, arch):
        response = requests.post(
            base.rstrip('/') + '/' + ref,
            json={'arch': arch, 'image': component},
            headers={'Content-Type': 'application/json'},
            timeout=30)
        if response.status_code != 200:
            raise BuildError(
                'stable-version endpoint returned HTTP {} for {}'
                .format(response.status_code, component))
        return response.text
    return fetch


def main(argv=None):
    parser = argparse.ArgumentParser(description='Assemble a release manifest.')
    parser.add_argument('--version-file', default='release/VERSION')
    parser.add_argument('--components', help='checked-in name->tag JSON')
    parser.add_argument('--from-stable', action='store_true',
                        help='seed from the existing latest_stable_version endpoint')
    parser.add_argument('--stable-base', default='https://functions-proxy.flexiblevision.com/')
    parser.add_argument('--stable-ref', default='latest_stable_version')
    parser.add_argument('--valid-days', type=int, default=DEFAULT_VALID_DAYS)
    # No --notes here on purpose. Notes are added by release/prepare.py at
    # signing time, where a human is guaranteed to be present and the
    # requirement can be enforced. A --notes flag here would be a second path
    # that quietly allows an empty one.
    parser.add_argument('--previous', help='previous manifest, for the change summary')
    parser.add_argument('--out', default='-')
    args = parser.parse_args(argv)

    if bool(args.components) == bool(args.from_stable):
        parser.error('pass exactly one of --components or --from-stable')

    try:
        with open(args.version_file) as handle:
            version_text = handle.read()

        if args.from_stable:
            tags = components_from_stable(
                _stable_fetcher(args.stable_base, args.stable_ref))
            features = {}
        else:
            with open(args.components) as handle:
                tags, features = components_from_file(handle.read())

        previous = None
        if args.previous and os.path.exists(args.previous):
            with open(args.previous) as handle:
                previous = manifest_mod.loads(handle.read())

        document, build_no = build(
            version_text=version_text,
            existing_tags=git_release_tags(),
            flexrun_commit=git_head(),
            tags=tags,
            features=features,
            resolver=registry_mod.DockerHubResolver(),
            now=datetime.datetime.utcnow(),
            valid_days=args.valid_days,
        )

        # Record what moved, so the candidate is never empty and the signer is
        # only asked for the part a machine cannot produce.
        from . import prepare as prepare_mod
        derived = prepare_mod.derive_notes(previous, document)
        notes = manifest_mod.blank_notes()
        notes.update(derived)
        document['notes'] = manifest_mod.normalise_notes(notes)
    except (BuildError, manifest_mod.ManifestError, registry_mod.RegistryError) as exc:
        sys.stderr.write('release build failed: {}\n'.format(exc))
        return 1

    raw = manifest_mod.canonical_bytes(document)
    if args.out == '-':
        sys.stdout.write(raw.decode('utf-8'))
    else:
        with open(args.out, 'wb') as handle:
            handle.write(raw)

    summary = diff_summary(previous, document)
    sys.stderr.write('release {} (counter {})\n'.format(document['release'], build_no))
    sys.stderr.write('  changed:   {}\n'.format(', '.join(summary['changed']) or 'none'))
    sys.stderr.write('  unchanged: {}\n'.format(len(summary['unchanged'])))
    sys.stderr.write('  not test-gated: {}\n'.format(
        ', '.join(manifest_mod.ungated(document))))
    sys.stderr.write('  next: python -m release.prepare (adds the required '
                     'notes), then sign, then promote a channel\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
