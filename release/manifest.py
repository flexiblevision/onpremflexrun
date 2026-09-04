"""Build and serialise a FlexRun release manifest.

A release today is a mutable value served by a cloud function, resolved
independently per image at apply time. That means no artifact anywhere records
what a release *is*, so a release cannot be reproduced, reviewed, or rolled
back, and a device can end up running a combination of container versions that
was never tested together.

A manifest replaces that with one document naming every component by immutable
digest. It is a few hundred bytes; the images are gigabytes and are never
signed directly. Because the manifest names each image by digest, and a
registry cannot serve different bytes under the same digest, one signature over
the manifest transitively covers every image in it.

Serialisation is canonical JSON, not YAML. A signature is over bytes, so the
encoding has to be deterministic: same content in, same bytes out, on any
machine and any library version. Sorted keys, fixed separators, no trailing
whitespace, UTF-8, newline-terminated.
"""
import datetime
import json
import re

SCHEMA = 'flexrun.release/v2'

# Foundational services: every device runs all of these, so a release must pin
# every one of them. The repository is "fvonprem/<arch>-<name>", which is why a
# manifest is arch-aware.
FOUNDATIONAL = (
    'backend',
    'frontend',
    'prediction',
    'predictlite',
    'vision',
    'nodecreator',
    'visiontools',
    'vernemq',
)

# Provenance per image, so a green release does not read as "all tested":
#   built     our source, gated on a test suite in its own repo
#   vendored  a fork we pin but never test (prediction, vernemq, nodecreator)
#   untested  our source, but no test suite exists yet
# Pinning gives integrity for all three; only "built" is evidence of testing.
VENDORED = frozenset({'prediction', 'vernemq', 'nodecreator'})

# Ours, but with no test gate today. Marked rather than quietly called built, so
# the gap shows on the signing screen until someone closes it. Move a name to
# "built" when its CI runs green, not when its CI merely exists.
#   frontend     suite exists; react-scripts 2.1.8 cannot transform date-fns/esm
#                so 8 of 11 suites fail. Needs a toolchain upgrade.
#   predictlite  its 5 test files are stale copies needing PySpin (proprietary)
#                and a caputils module that is not in the repo.
#   visiontools  no tests at all.
UNTESTED = frozenset({'frontend', 'predictlite', 'visiontools'})

BUILT = 'built'
VENDORED_PROVENANCE = 'vendored'
UNTESTED_PROVENANCE = 'untested'

NOT_TEST_GATED = VENDORED | UNTESTED

# vernemq is tagged by environment ("local"/"prod"), not by version, so its tag
# must never be compared as a release number.
ENV_TAGGED = frozenset({'vernemq'})

# Feature services (eventor, ocr, audio, assembly, ...) are not enumerated: the
# set grows, and a hardcoded list would silently drop a new one from pinning.
# Any feature named in a release is pinned; a device applies what it enables.
TIER_FOUNDATIONAL = 'foundational'
TIER_FEATURE = 'feature'

ARCHES = ('x86', 'arm')

# Components that do not exist for an arch yet. Declared, never inferred: a tag
# that is simply missing stays a hard error, because that is how ARM devices
# came to silently never upgrade visiontools. Delete the entry when the image
# ships for that arch.
NOT_ON_ARCH = {
    # visiontools: no arm image published.
    # vernemq: fvonprem/arm-vernemq does not exist in the registry at all.
    'arm': frozenset({'visiontools', 'vernemq'}),
}


def foundational_for_arch(arch):
    """What a release must pin for this arch."""
    absent = NOT_ON_ARCH.get(arch, frozenset())
    return tuple(c for c in FOUNDATIONAL if c not in absent)

# Kept as an alias: the old name meant "the required set".
COMPONENTS = FOUNDATIONAL

REGISTRY_NAMESPACE = 'fvonprem'

# Notes live inside the signed manifest so the notes an operator approves are
# the notes the signer wrote - anywhere else, someone who cannot forge a digest
# could still mislabel a release. Structured, not prose, so the UI never parses
# text and a missing field cannot pass unnoticed.
#
#   summary/impact/security   written by a human; impact decides whether an
#                             operator approves now or waits for shift change
#   changed/unchanged/features_*   derived from the diff
#   reference                 optional, engineer-facing
NOTES_HUMAN_FIELDS = ('summary', 'impact', 'security')
NOTES_DERIVED_FIELDS = ('changed', 'unchanged', 'features_added', 'features_removed')
NOTES_OPTIONAL_FIELDS = ('reference',)
NOTES_FIELDS = NOTES_HUMAN_FIELDS + NOTES_DERIVED_FIELDS + NOTES_OPTIONAL_FIELDS


def blank_notes():
    return {'summary': '', 'impact': '', 'security': False,
            'changed': [], 'unchanged': 0,
            'features_added': [], 'features_removed': []}


def normalise_notes(notes):
    """Accept a notes object, reject anything that is not one.

    A bare string used to be valid (schema v1). Rejecting it with a pointed
    message beats silently dropping the impact and security fields.
    """
    if notes is None:
        return blank_notes()
    if isinstance(notes, str):
        raise ManifestError(
            'notes must be an object, not a string - schema v2 carries '
            'summary, impact and security separately so the UI does not have '
            'to parse prose and a missing field cannot pass unnoticed')
    if not isinstance(notes, dict):
        raise ManifestError('notes must be an object, got {}'.format(type(notes).__name__))

    unknown = sorted(set(notes) - set(NOTES_FIELDS))
    if unknown:
        raise ManifestError('unknown notes field(s): {}'.format(', '.join(unknown)))

    merged = blank_notes()
    merged.update(notes)

    if not isinstance(merged['security'], bool):
        raise ManifestError(
            'notes.security must be true or false, got {!r}'.format(merged['security']))
    if not isinstance(merged['unchanged'], int) or isinstance(merged['unchanged'], bool):
        raise ManifestError(
            'notes.unchanged must be an integer, got {!r}'.format(merged['unchanged']))
    for field in ('summary', 'impact'):
        if not isinstance(merged[field], str):
            raise ManifestError('notes.{} must be text'.format(field))
    for field in ('changed', 'features_added', 'features_removed'):
        if not isinstance(merged[field], list):
            raise ManifestError('notes.{} must be a list'.format(field))
    return merged


def notes_shortfall(manifest):
    """What is missing before this manifest may be signed.

    Enforced at signing rather than on the device: signing is already a
    deliberate human act, so a gate there is free and guarantees notes exist,
    whereas a device refusing on absent notes would make an empty field able to
    block updates fleet-wide.
    """
    notes = (manifest.get('notes') or {})
    if isinstance(notes, str):
        return ['notes are a bare string; schema v2 expects an object']

    missing = []
    if not str(notes.get('summary', '')).strip():
        missing.append('summary - one sentence on why this release matters')

    changed = notes.get('changed') or []
    if changed and not str(notes.get('impact', '')).strip():
        missing.append('impact - what the operator will see, and for how long')

    if notes.get('security') and not str(notes.get('summary', '')).strip():
        missing.append('summary is required on a security release')

    return missing


_DIGEST_RE = re.compile(r'^sha256:[0-9a-f]{64}$')
# MAJOR.MINOR ('1.0'), or MAJOR.MINOR.BUILD for releases cut before the
# version scheme moved to two parts. Both are accepted so an older
# manifest a device still has in its history stays parseable.
_RELEASE_RE = re.compile(r'^\d+\.\d+(\.\d+)?$')
_COMMIT_RE = re.compile(r'^[0-9a-f]{40}$')


class ManifestError(Exception):
    """Raised when a manifest cannot be built or is structurally invalid."""


def repository(arch, component):
    return '{}/{}-{}'.format(REGISTRY_NAMESPACE, arch, component)


def _iso(moment):
    """UTC, second precision, explicit Z. Stable across platforms."""
    return moment.replace(microsecond=0).strftime('%Y-%m-%dT%H:%M:%SZ')


def provenance_of(component):
    if component in VENDORED:
        return VENDORED_PROVENANCE
    if component in UNTESTED:
        return UNTESTED_PROVENANCE
    return BUILT


def per_arch_tags(tags, arches=ARCHES):
    """Normalise a tag map to {arch: {component: tag}}.

    Accepts either shape, because both occur:
      {'backend': '1.97'}                     same tag on every arch
      {'x86': {...}, 'arm': {...}}            per-arch, which is the real
                                              fleet - x86 backend is 1.97
                                              while arm is 1.93
    """
    if not isinstance(tags, dict) or not tags:
        raise ManifestError('tags must be a non-empty object')

    # Tested against every known arch, not the subset being built. A per-arch
    # map covering both is still a per-arch map when only x86 is wanted -
    # reading it as a flat component map instead would look for components
    # literally called "x86" and "arm".
    keyed_by_arch = set(tags) <= set(ARCHES)
    if not keyed_by_arch:
        # A flat map means "this tag on every arch", so components an arch does
        # not have are dropped here. Naming one explicitly under an arch stays
        # an error - see build_manifest.
        return {arch: {name: tag for name, tag in tags.items()
                       if name not in NOT_ON_ARCH.get(arch, frozenset())}
                for arch in arches}

    missing_arch = sorted(set(arches) - set(tags))
    if missing_arch:
        raise ManifestError(
            'no tags given for arch(es): {} - a release that pins one '
            'architecture leaves the other with nothing to apply'
            .format(', '.join(missing_arch)))

    resolved = {}
    for arch in arches:
        if not isinstance(tags[arch], dict) or not tags[arch]:
            raise ManifestError(
                'tags for {} must be a non-empty object of name -> tag'.format(arch))
        resolved[arch] = dict(tags[arch])
    return resolved


def build_manifest(release, counter, tags, flexrun_commit, resolver,
                   now, valid_days=90, notes=None, arches=ARCHES, features=None):
    """Assemble a release manifest.

    release        dotted version string, e.g. "1.9.3"
    counter        monotonic integer; a device refuses anything not greater
                   than what it already runs, which is what stops a rollback
                   to a known-bad release being replayed at it
    tags           {component: tag}, or {arch: {component: tag}} where the
                   architectures are on different version streams. Components
                   in ENV_TAGGED carry a channel name rather than a version.
    flexrun_commit full 40-char sha of the orchestration tree this release
                   expects; without it the scripts and the container versions
                   can disagree
    resolver       callable(repository, tag) -> "sha256:..."
    now            datetime, injected so output is reproducible and testable
    valid_days     lifetime; past notAfter a device reports itself stale rather
                   than sitting quietly on old code
    """
    if not _RELEASE_RE.match(str(release)):
        raise ManifestError(
            'release must look like 1.0 or 1.0.3, got {!r}'.format(release))

    if not isinstance(counter, int) or isinstance(counter, bool) or counter < 1:
        raise ManifestError(
            'counter must be a positive integer, got {!r}'.format(counter))

    if not _COMMIT_RE.match(str(flexrun_commit or '')):
        raise ManifestError(
            'flexrun_commit must be a full 40-character sha, got {!r}'
            .format(flexrun_commit))

    arch_tags = per_arch_tags(tags, arches)

    for arch in arches:
        expected = set(foundational_for_arch(arch))
        given = set(arch_tags[arch])

        missing = sorted(expected - given)
        if missing:
            raise ManifestError('no tag given for: {} on {}'.format(
                ', '.join(missing), arch))

        # A tag for something that does not exist on this arch pins an image
        # nobody can pull, so say which arch and why rather than failing later
        # in the registry.
        absent = sorted(given & NOT_ON_ARCH.get(arch, frozenset()))
        if absent:
            raise ManifestError(
                '{} is not built for {} - remove it from that arch, or update '
                'NOT_ON_ARCH if it now ships'.format(', '.join(absent), arch))

        unknown = sorted(given - expected - set(FOUNDATIONAL))
        if unknown:
            raise ManifestError(
                'unknown foundational component(s) on {}: {} - pass optional '
                'services as features='.format(arch, ', '.join(unknown)))

    features = per_arch_tags(features, arches) if features else \
        {arch: {} for arch in arches}
    for arch in arches:
        overlap = sorted(set(features[arch]) & set(FOUNDATIONAL))
        if overlap:
            raise ManifestError(
                'component(s) declared both foundational and feature: {}'
                .format(', '.join(overlap)))

    images = {}
    for arch in arches:
        tiers = [(name, tag, TIER_FOUNDATIONAL)
                 for name, tag in arch_tags[arch].items()]
        tiers += [(name, tag, TIER_FEATURE)
                  for name, tag in features[arch].items()]

        per_arch = {}
        for component, tag, tier in sorted(tiers):
            tag = str(tag)
            if not tag:
                raise ManifestError(
                    'empty tag for {} - an empty version is how the old '
                    'pipeline silently upgraded nothing'.format(component))
            repo = repository(arch, component)
            digest = resolver(repo, tag)
            if not _DIGEST_RE.match(str(digest or '')):
                raise ManifestError(
                    'resolver returned an invalid digest for {}:{} -> {!r}'
                    .format(repo, tag, digest))
            per_arch[component] = {
                'repository': repo,
                'tag': tag,
                'digest': digest,
                'tier': tier,
                'provenance': provenance_of(component),
            }
        images[arch] = per_arch

    document = {
        'schema': SCHEMA,
        'release': str(release),
        'counter': counter,
        'created': _iso(now),
        'notAfter': _iso(now + datetime.timedelta(days=valid_days)),
        'flexrun': {
            'repository': 'https://github.com/flexiblevision/onpremflexrun',
            'commit': str(flexrun_commit),
        },
        'images': images,
        'notes': normalise_notes(notes),
    }

    # A single-arch manifest names its architecture. Counters are per arch, so a
    # device has to be able to tell that a manifest is for its own arch before
    # comparing counters at all - otherwise an x86 counter 7 would look newer
    # than an arm device's counter 5 and be accepted.
    if len(images) == 1:
        document['arch'] = next(iter(images))
    return document


def canonical_bytes(manifest):
    """Deterministic serialisation. This is what gets signed and verified."""
    return (json.dumps(manifest, sort_keys=True, separators=(',', ':'),
                       ensure_ascii=False) + '\n').encode('utf-8')


def loads(raw):
    """Parse manifest bytes, rejecting anything structurally wrong."""
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8')
    try:
        manifest = json.loads(raw)
    except ValueError as exc:
        raise ManifestError('manifest is not valid JSON: {}'.format(exc))
    if not isinstance(manifest, dict):
        raise ManifestError('manifest must be a JSON object')
    validate(manifest)
    return manifest


def validate(manifest):
    """Structural checks only. Signature and freshness live in verify.py."""
    if manifest.get('schema') != SCHEMA:
        raise ManifestError(
            'unsupported schema {!r}, expected {!r}'
            .format(manifest.get('schema'), SCHEMA))

    for field in ('release', 'counter', 'created', 'notAfter', 'flexrun', 'images'):
        if field not in manifest:
            raise ManifestError('manifest is missing {!r}'.format(field))

    if not isinstance(manifest['counter'], int) or isinstance(manifest['counter'], bool):
        raise ManifestError('counter must be an integer')

    commit = (manifest.get('flexrun') or {}).get('commit', '')
    if not _COMMIT_RE.match(str(commit)):
        raise ManifestError('flexrun.commit is not a full sha: {!r}'.format(commit))

    images = manifest['images']
    if not isinstance(images, dict) or not images:
        raise ManifestError('images must be a non-empty object')

    for arch, per_arch in images.items():
        if not isinstance(per_arch, dict) or not per_arch:
            raise ManifestError('images.{} is empty'.format(arch))
        for component, entry in per_arch.items():
            if not isinstance(entry, dict):
                raise ManifestError(
                    'images.{}.{} must be an object'.format(arch, component))
            for key in ('repository', 'tag', 'digest'):
                if not entry.get(key):
                    raise ManifestError(
                        'images.{}.{} is missing {}'.format(arch, component, key))
            if not _DIGEST_RE.match(str(entry['digest'])):
                raise ManifestError(
                    'images.{}.{} has a malformed digest: {!r}'
                    .format(arch, component, entry['digest']))

    arch = manifest.get('arch')
    if arch is not None and set(images) != {arch}:
        raise ManifestError(
            'manifest declares arch {!r} but carries images for {} - a release '
            'is for one architecture'.format(arch, sorted(images)))

    normalise_notes(manifest.get('notes'))
    return manifest


def pinned_reference(manifest, arch, component):
    """The reference a device should pull: by digest, never by tag.

    Including the tag before the @ is cosmetic - the digest is what the
    registry resolves - but it makes `docker ps` output readable.
    """
    entry = manifest['images'][arch][component]
    return '{}@{}'.format(entry['repository'], entry['digest'])


def components_for(manifest, arch):
    if arch not in manifest.get('images', {}):
        raise ManifestError(
            'release {} has no images for arch {!r} (has: {})'
            .format(manifest.get('release'), arch,
                    ', '.join(sorted(manifest.get('images', {})))))
    return manifest['images'][arch]


def foundational_for(manifest, arch):
    """Everything a device must run. Applied unconditionally."""
    return {name: entry
            for name, entry in components_for(manifest, arch).items()
            if entry.get('tier', TIER_FOUNDATIONAL) == TIER_FOUNDATIONAL}


def features_for(manifest, arch):
    """Optional services. A device applies only the ones it has enabled."""
    return {name: entry
            for name, entry in components_for(manifest, arch).items()
            if entry.get('tier') == TIER_FEATURE}


def applicable(manifest, arch, enabled_features=()):
    """What this particular device should end up running.

    A feature that is enabled on the device but absent from the release is
    reported rather than skipped: silently leaving a running container
    un-upgraded is how a device ends up on a combination nobody chose.
    """
    per_arch = components_for(manifest, arch)
    enabled = set(enabled_features or ())

    unpinned = sorted(enabled - set(per_arch))
    if unpinned:
        raise ManifestError(
            'release {} does not pin enabled feature(s): {} - the device would '
            'keep running an unpinned container'
            .format(manifest.get('release'), ', '.join(unpinned)))

    return {name: entry for name, entry in per_arch.items()
            if entry.get('tier', TIER_FOUNDATIONAL) == TIER_FOUNDATIONAL
            or name in enabled}


def ungated(manifest, arch='x86'):
    """Components pinned but not test-gated, so a release can say so out loud.

    Vendored forks and our own untested code both land here. The signature
    proves nobody substituted the bytes; it is not evidence anybody tested them.
    """
    return sorted(name for name, entry in components_for(manifest, arch).items()
                  if entry.get('provenance') in (VENDORED_PROVENANCE,
                                                 UNTESTED_PROVENANCE))
