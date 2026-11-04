"""Check that a release only pins images CI built from master.

The rule this enforces: an image is releasable only if a CI run on master
produced it. A laptop build, a dirty tree, or a hand-pushed tag should not be
able to reach a factory floor.

Nothing here needs to ask GitHub which branch a commit is on. Each repo's CI
gates its image job on

    if: github.ref == 'refs/heads/master' && github.event_name == 'push'

so an image CI published is from master by construction. What is left to verify
is that CI - and not a laptop - is what published it, and the OCI revision label
is the evidence: build.sh refuses a dirty tree outright, and CI re-checks the
label against github.sha before pushing.

WARNING, not a refusal, for now. Every image the fleet currently runs predates
this and carries no label, so enforcing today would block release 1 outright.
Release 1 has to grandfather them in. Flip STRICT once every component has been
rebuilt through CI at least once - the shortfall printed by describe() is the
list of what is still in the way.
"""

MISSING = 'missing'
DIRTY = 'dirty'
OK = 'ok'

REVISION_LABEL = 'org.opencontainers.image.revision'
SOURCE_LABEL = 'org.opencontainers.image.source'

# Labels every image inherits from its base. Present on an image that was never
# stamped by us, so treating them as ours would report a clean bill of health
# for exactly the images that have no provenance at all.
INHERITED = frozenset({
    'org.opencontainers.image.ref.name',
    'org.opencontainers.image.version',
})

STRICT = False


def classify(labels):
    """OK / DIRTY / MISSING for one image's labels."""
    revision = (labels or {}).get(REVISION_LABEL)
    if not revision:
        return MISSING, None
    revision = str(revision).strip()
    if not revision or revision == 'unknown':
        return MISSING, None
    # build.sh appends -dirty when it was told to build anyway. A CI checkout is
    # always clean, so this can only mean the image came from a working tree.
    if revision.endswith('-dirty'):
        return DIRTY, revision
    return OK, revision


def audit(manifest, fetch_labels):
    """Classify every image in a manifest.

    fetch_labels(repository, tag) -> dict. Returns a list of records, one per
    image, in a stable order so the output does not churn between runs.
    """
    records = []
    for arch in sorted(manifest.get('images') or {}):
        for component in sorted(manifest['images'][arch]):
            image = manifest['images'][arch][component]
            labels = fetch_labels(image['repository'], image['tag']) or {}
            ours = {k: v for k, v in labels.items() if k not in INHERITED}
            status, revision = classify(ours)
            records.append({
                'arch': arch,
                'component': component,
                'repository': image['repository'],
                'tag': image['tag'],
                'status': status,
                'revision': revision,
                'source': ours.get(SOURCE_LABEL),
            })
    return records


def shortfall(records):
    """The records that would block a release once STRICT is on."""
    return [r for r in records if r['status'] != OK]


def describe(records, stream, strict=None):
    """Print the audit. Returns True if it should block the cut."""
    strict = STRICT if strict is None else strict
    blocking = shortfall(records)

    for record in records:
        if record['status'] == OK:
            detail = record['revision'][:12]
        elif record['status'] == DIRTY:
            detail = 'built from a dirty tree ({})'.format(record['revision'])
        else:
            detail = 'no revision label - cannot tell what source built it'
        stream.write('  [{:4}] {:<12} {}:{}  {}\n'.format(
            'ok' if record['status'] == OK else 'warn',
            record['component'], record['repository'], record['tag'], detail))

    if not blocking:
        stream.write('\nevery pinned image records the commit that built it.\n')
        return False

    stream.write(
        '\n{} of {} images cannot be traced to a CI build from master.\n'
        .format(len(blocking), len(records)))
    if strict:
        stream.write(
            'Refusing: a release must be able to say what source produced it.\n'
            'Merge to master so CI builds and labels the image, then pin the '
            'tag it published.\n')
        return True

    stream.write(
        'Allowed for now - these predate the build scripts stamping a commit, '
        'and\nrelease 1 has to pin what the fleet already runs. Each one stops '
        'warning\nonce it is rebuilt through CI on master.\n')
    return False
