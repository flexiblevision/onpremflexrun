"""Cut a release, guided.

One command instead of four, and - more to the point - every prerequisite is
checked before any work happens. The failure this exists to prevent is getting
through digest resolution and notes, then discovering cosign is not installed.

    python -m release.cut --from-stable
    python -m release.cut --components release/components.json

Preflight, then: resolve digests -> notes (your editor) -> sign -> verify ->
print what to paste into the cloud function.
"""
import argparse
import base64
import datetime
import json
import os
import shutil
import subprocess
import sys

from . import build_release as build_mod
from . import candidates as candidates_mod
from . import manifest as manifest_mod
from . import prepare as prepare_mod
from . import provenance as provenance_mod
from . import registry as registry_mod
from . import sign as sign_mod
from . import verify as verify_mod

DEFAULT_WORK_DIR = '.release-work'
DEFAULT_COMPONENTS = 'release/components.json'
STABLE_BASE = 'https://functions-proxy.flexiblevision.com/'
STABLE_REF = 'latest_stable_version'


class CutError(Exception):
    pass


# --- preflight --------------------------------------------------------------

class Check:
    def __init__(self, label, ok, detail='', fix='', fatal=True):
        self.label = label
        self.ok = ok
        self.detail = detail
        self.fix = fix
        self.fatal = fatal


def _git(args, run=None):
    runner = run or (lambda argv: subprocess.run(
        argv, capture_output=True, text=True, check=False))
    return runner(['git'] + args)


def kms_reachable(key_ref, run=None):
    """Can we actually read the key version? Returns (ok, detail)."""
    try:
        fields = sign_mod.parse_kms_key(key_ref)
    except sign_mod.SignError as exc:
        return False, str(exc)

    runner = run or (lambda argv: subprocess.run(
        argv, capture_output=True, text=True, check=False))
    result = runner([
        'gcloud', 'kms', 'keys', 'versions', 'describe', fields['versions'],
        '--project', fields['projects'], '--location', fields['locations'],
        '--keyring', fields['keyRings'], '--key', fields['cryptoKeys'],
        '--format', 'value(state,algorithm)',
    ])
    if result.returncode != 0:
        message = (result.stderr or result.stdout or '').strip()
        if 'Reauthentication' in message or 'auth' in message.lower():
            return False, 'gcloud credentials expired'
        return False, message.splitlines()[0][:90] if message else 'unreachable'

    state = (result.stdout or '').strip().replace('\t', ' ')
    if 'ENABLED' not in state:
        return False, 'key version is {}, not ENABLED'.format(state or 'unknown')
    return True, state


def preflight(key_path, version_text, allow_dirty=False, need_credentials=True,
              which=shutil.which, environ=None, run=None,
              use_docker_login=False, config_path=None, arch='x86'):
    """Everything that must be true before a release is worth starting."""
    env = environ if environ is not None else os.environ
    checks = []

    # Only required when cosign is the delegate. KMS and openssl keys do not
    # need it, and devices never do - verify.py defaults to local_verify.
    needs_cosign = sign_mod.signer_for(key_path) is sign_mod.cosign_sign
    cosign = which('cosign')
    checks.append(Check(
        'cosign installed', bool(cosign) or not needs_cosign, cosign or 'not needed',
        'https://github.com/sigstore/cosign/releases',
        fatal=needs_cosign))

    if str(key_path or '').startswith(sign_mod.KMS_PREFIX):
        try:
            fields = sign_mod.parse_kms_key(key_path)
            detail = '{}/{} v{}'.format(fields['keyRings'],
                                        fields['cryptoKeys'],
                                        fields['versions'])
            ok, fix = True, ''
        except sign_mod.SignError as exc:
            detail, ok, fix = str(exc), False, (
                'gcpkms://projects/P/locations/L/keyRings/R/cryptoKeys/K/versions/V')
        checks.append(Check('KMS key reference', ok, detail, fix))

        gcloud = which('gcloud')
        checks.append(Check(
            'gcloud installed', bool(gcloud), gcloud or '',
            'Cloud KMS signing shells out to gcloud kms asymmetric-sign'))

        # Reaching the key, not just having gcloud. An expired token surfaces
        # here rather than after digest resolution and writing notes - which is
        # exactly where it surfaced the first time this was run for real.
        # A read does not prove signerVerifier, only that auth and the key
        # reference are good; permission is proven at signing.
        if gcloud and ok:
            reachable, detail = kms_reachable(key_path, run=run)
            checks.append(Check(
                'KMS key reachable', reachable, detail,
                'gcloud auth login, then re-run. Also check the project and '
                'that the key version exists'))
    else:
        checks.append(Check(
            'signing key readable', bool(key_path) and os.path.isfile(key_path),
            key_path or '(none given)',
            'cosign generate-key-pair, or pass --key with a gcpkms:// or '
            'pkcs11: reference'))

    user = env.get('DOCKERHUB_USERNAME')
    token = env.get('DOCKERHUB_TOKEN')
    source = 'environment'
    if not (user and token) and use_docker_login:
        user, token = registry_mod.docker_config_credentials(config_path)
        source = 'docker login'
    checks.append(Check(
        'Docker Hub credentials', bool(user and token),
        '{} (from {})'.format(user, source) if user else 'not set',
        'export DOCKERHUB_USERNAME and DOCKERHUB_TOKEN, or pass '
        '--use-docker-login to reuse an existing docker login - the fvonprem '
        'repos are private, so digest resolution returns HTTP 401 without them',
        fatal=need_credentials))

    try:
        major, minor = build_mod.parse_version_file(version_text)
        checks.append(Check('release/VERSION', True, '{}.{}'.format(major, minor)))
    except build_mod.BuildError as exc:
        checks.append(Check('release/VERSION', False, str(exc),
                            'write MAJOR.MINOR only - CI owns the build number'))

    head = _git(['rev-parse', 'HEAD'], run)
    checks.append(Check(
        'git HEAD', head.returncode == 0, (head.stdout or '').strip()[:12],
        'a release pins the flexrun commit, so it has to be resolvable'))

    # The counter is derived from remote tags and reserved by pushing one, so an
    # unreachable or read-only remote has to fail here rather than after digests
    # are resolved and notes are written.
    try:
        remote_tags = build_mod.remote_release_tags(run=run)
        highest = build_mod.next_build(remote_tags, arch) - 1
        checks.append(Check(
            'release tags on remote', True,
            'highest release/{}/{} -> next is {}'.format(arch, highest, highest + 1)
            if highest else 'none yet for {} -> next is 1'.format(arch)))
    except build_mod.BuildError as exc:
        checks.append(Check(
            'release tags on remote', False, str(exc).splitlines()[0],
            'the counter lives in remote git tags - a release cannot be cut '
            'without reaching the remote'))

    status = _git(['status', '--porcelain'], run)
    dirty = bool((status.stdout or '').strip())
    checks.append(Check(
        'working tree clean', (not dirty) or allow_dirty,
        '{} modified path(s)'.format(len((status.stdout or '').strip().splitlines()))
        if dirty else 'clean',
        'commit or stash first - the manifest pins HEAD, and a dirty tree means '
        'the pinned commit is not what you tested. --allow-dirty to override'))

    return checks


def render_checks(checks, stream=sys.stderr):
    width = max(len(c.label) for c in checks)
    for check in checks:
        mark = 'ok  ' if check.ok else ('FAIL' if check.fatal else 'warn')
        stream.write('  [{}] {:<{w}}  {}\n'.format(
            mark, check.label, check.detail, w=width))
    blocking = [c for c in checks if not c.ok and c.fatal]
    for check in blocking:
        stream.write('\n{}:\n    {}\n'.format(check.label, check.fix))
    return blocking


# --- component set ----------------------------------------------------------

def stable_fetcher(base=STABLE_BASE, ref=STABLE_REF):
    import requests

    def fetch(component, arch):
        response = requests.post(
            base.rstrip('/') + '/' + ref,
            json={'arch': arch, 'image': component},
            headers={'Content-Type': 'application/json'}, timeout=30)
        if response.status_code != 200:
            return None
        return response.text.strip()
    return fetch


def tags_from_stable(fetch, arches=manifest_mod.ARCHES, overrides=None):
    """Per-arch tags from the endpoint, skipping what an arch does not have.

    The endpoint does not serve every component - vernemq is absent entirely
    because it carries a channel name, not a version - so anything it cannot
    answer for has to be supplied and is reported rather than guessed.
    """
    overrides = overrides or {}
    resolved, unresolved = {}, []

    for arch in arches:
        per_arch = {}
        for component in manifest_mod.foundational_for_arch(arch):
            override = overrides.get(component)
            if override:
                per_arch[component] = override
                continue
            value = fetch(component, arch)
            if value:
                per_arch[component] = value
            else:
                unresolved.append('{} on {}'.format(component, arch))
        resolved[arch] = per_arch

    return resolved, unresolved


def describe_tags(tags, stream=sys.stderr):
    for arch in sorted(tags):
        stream.write('  {}:\n'.format(arch))
        for name in sorted(tags[arch]):
            stream.write('    {:<14} {}\n'.format(name, tags[arch][name]))


# --- the run ----------------------------------------------------------------

def cut(tags, version_text, work_dir, key_path, previous_raw=None,
        resolver=None, now=None, editor=None, confirm=None,
        signer=None, arch='x86', existing_tags=None, head=None,
        use_docker_login=False, reserve=None, fetch_labels=None,
        strict_provenance=None):
    """Resolve digests, reserve the counter, take notes, sign.

    Returns (manifest bytes, signature).
    """
    if not os.path.isdir(work_dir):
        os.makedirs(work_dir)

    resolver = resolver or registry_mod.DockerHubResolver(
        use_docker_config=use_docker_login)
    now = now or datetime.datetime.utcnow()
    reserve = reserve if reserve is not None else build_mod.reserve_build

    # Remote tags, not local: the counter has to be unique across everyone who
    # can cut, and only the remote knows that.
    if existing_tags is None:
        existing_tags = build_mod.remote_release_tags()
    if head is None:
        head = build_mod.git_head()

    sys.stderr.write('\nresolving digests (one registry call per image)...\n')
    document, build_no = build_mod.build(
        version_text=version_text, existing_tags=existing_tags,
        flexrun_commit=head, tags=tags, resolver=resolver, now=now, arch=arch)

    previous = manifest_mod.loads(previous_raw) if previous_raw else None
    summary = build_mod.diff_summary(previous, document, arch=arch)
    sys.stderr.write('\nrelease {} (counter {})\n'.format(
        document['release'], build_no))
    sys.stderr.write('  changed:        {}\n'.format(
        ', '.join(summary['changed']) or 'nothing'))
    sys.stderr.write('  unchanged:      {}\n'.format(len(summary['unchanged'])))
    if summary['added']:
        sys.stderr.write('  added:          {}\n'.format(', '.join(summary['added'])))
    if summary['removed']:
        sys.stderr.write('  removed:        {}\n'.format(', '.join(summary['removed'])))
    sys.stderr.write('  not test-gated: {}\n'.format(
        ', '.join(manifest_mod.ungated(document, arch))))

    candidate_path = os.path.join(work_dir, 'candidate.json')
    with open(candidate_path, 'wb') as handle:
        handle.write(manifest_mod.canonical_bytes(document))

    # Can each pinned image say what source produced it? Before the reservation,
    # so a release that cannot answer that costs nothing to abandon.
    if fetch_labels is None and hasattr(resolver, 'labels'):
        fetch_labels = resolver.labels
    if fetch_labels is not None:
        sys.stderr.write('\nprovenance of the pinned images:\n')
        records = provenance_mod.audit(document, fetch_labels)
        if provenance_mod.describe(records, sys.stderr, strict=strict_provenance):
            raise CutError('release refused: images without a traceable build')

    # Before the notes and the signature, because this is what makes the counter
    # trustworthy. Abandoning the cut from here on leaves a gap in the sequence,
    # which devices cannot see; reusing a number would be invisible here and
    # break anti-rollback on every device that had already taken it.
    ref = reserve(build_no, head, arch,
                  message='release {} ({})'.format(document['release'], arch))
    sys.stderr.write('\nreserved {} on the remote - counter {} is now spent, '
                     'whether or not this cut finishes\n'.format(ref, build_no))

    sys.stderr.write('\nnotes are required before signing - opening your editor\n')
    kwargs = {'arch': arch}
    if editor is not None:
        kwargs['editor'] = editor
    signable = prepare_mod.prepare(
        manifest_mod.canonical_bytes(document),
        previous_raw=previous_raw, **kwargs)

    signable_path = os.path.join(work_dir, 'manifest.json')
    with open(signable_path, 'wb') as handle:
        handle.write(signable)

    sign_kwargs = {'arch': arch}
    if confirm is not None:
        sign_kwargs['confirm'] = confirm
    if signer is not None:
        sign_kwargs['signer'] = signer
    signature = sign_mod.sign(signable, signable_path, key_path, **sign_kwargs)

    signature_path = signable_path + sign_mod.SIGNATURE_SUFFIX
    with open(signature_path, 'wb') as handle:
        handle.write(signature)

    return signable, signature


def verify_as_a_device_would(manifest_path, signature_path, public_key_path):
    """Check the signature exactly the way a device will.

    Deliberately not `cosign verify-blob`: cosign 3 wants its own bundle format
    and reaches for the transparency log, so it would be checking something
    other than what ships. Using the device's own verifier means a pass here
    means a pass in the field.
    """
    try:
        used = verify_mod.local_verify_any(
            manifest_path, signature_path, public_key_path)
        return True, 'verified with {}'.format(os.path.basename(used))
    except verify_mod.VerificationError as exc:
        return False, str(exc)


def write_components(path, tags, stream=sys.stderr):
    """Write a per-arch component file.

    Seeding this from the endpoint once, then editing it, is the difference
    between a release pinning what someone chose and a release inheriting
    whatever a mutable cloud value happens to hold. The file is reviewable, has
    an author, and can be reverted; the cloud value has none of those.
    """
    document = {'components': {arch: dict(sorted(tags[arch].items()))
                               for arch in sorted(tags)}}
    body = json.dumps(document, indent=2, sort_keys=True) + '\n'

    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, 'w') as handle:
        handle.write(body)

    stream.write('wrote {}\n'.format(path))
    for arch in sorted(tags):
        stream.write('  {}: {} component(s)\n'.format(arch, len(tags[arch])))
    stream.write('\nCommit it. Editing this file is how you choose what the next\n'
                 'release pins; --from-stable only reports what is deployed now.\n')
    return path


def compare_to_stable(tags, fetch, stream=sys.stderr):
    """Report where the chosen set differs from what devices run today.

    Not a gate - pinning something other than current stable is the normal case
    when rolling forward or back. But it should never be a surprise.
    """
    differences = []
    for arch in sorted(tags):
        for component in sorted(tags[arch]):
            chosen = tags[arch][component]
            current = fetch(component, arch)
            if current and str(current).strip() != str(chosen):
                differences.append((arch, component, str(current).strip(), chosen))

    if not differences:
        stream.write('  every pinned version matches current stable\n')
        return differences

    stream.write('  differs from current stable:\n')
    for arch, component, current, chosen in differences:
        stream.write('    {:<4} {:<14} stable {:<10} -> pinning {}\n'
                     .format(arch, component, current, chosen))
    return differences


def publish_block(signable, signature, counter, stream=sys.stderr, arch='x86'):
    """What to paste into release/cloudfunction/releases.py."""
    stream.write("\nAdd to RELEASES['{}'] in release/cloudfunction/releases.py:"
                 '\n\n'.format(arch))
    stream.write('        {}: {{\n'.format(counter))
    stream.write("            'manifest_b64': '{}',\n".format(
        base64.b64encode(signable).decode('ascii')))
    stream.write("            'signature':    '{}',\n".format(
        signature.decode('ascii').strip()))
    stream.write('        },\n\n')
    stream.write("Then point that arch's channel at it:  "
                 "CHANNELS['{}']['stable'] = {}\n".format(arch, counter))
    stream.write('and redeploy the function. Devices refuse anything not newer\n'
                 'than their own high-water mark, so a mistaken promote cannot\n'
                 'downgrade a device that moved past it.\n')


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Cut, sign and verify a release, with preflight checks.')
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--components', default=DEFAULT_COMPONENTS,
                        help='checked-in per-arch tag JSON (default: {})'
                             .format(DEFAULT_COMPONENTS))
    source.add_argument('--from-stable', action='store_true',
                        help='read current versions from the latest_stable_version '
                             'endpoint; for seeding --write-components, or a '
                             'release that deliberately matches what is deployed')
    parser.add_argument('--key', help='cosign key (file, or pkcs11:/kms: URI)')
    parser.add_argument('--public-key',
                        help='PEM file, or a trust-store directory, to verify '
                             'the signature the way a device will')
    parser.add_argument('--previous', help='the release currently promoted')
    parser.add_argument('--work-dir', default=DEFAULT_WORK_DIR)
    parser.add_argument('--version-file', default='release/VERSION')
    parser.add_argument('--arch', default='x86', help='which arch to summarise')
    parser.add_argument('--vernemq', default='dev',
                        help='vernemq channel; the endpoint does not serve it. '
                             'dev is the only published tag')
    parser.add_argument('--use-docker-login', action='store_true',
                        help='reuse ~/.docker/config.json instead of '
                             'DOCKERHUB_USERNAME/DOCKERHUB_TOKEN')
    parser.add_argument('--allow-dirty', action='store_true')
    parser.add_argument('--preflight-only', action='store_true')
    parser.add_argument('--strict-provenance', action='store_true',
                        help='refuse images with no CI-recorded commit, rather '
                             'than warning (see release/provenance.py)')
    parser.add_argument('--update-components', action='store_true',
                        help='find the newest CI-built version of each '
                             'component, write release/components.json, and '
                             'show what changed. Commit that diff to promote.')
    parser.add_argument('--write-components', metavar='PATH',
                        help='write the resolved component set to PATH and stop, '
                             'without cutting anything')
    args = parser.parse_args(argv)

    try:
        with open(args.version_file) as handle:
            version_text = handle.read()
    except OSError as exc:
        sys.stderr.write('cannot read {}: {}\n'.format(args.version_file, exc))
        return 1

    sys.stderr.write('preflight\n')
    checks = preflight(args.key, version_text, allow_dirty=args.allow_dirty,
                       need_credentials=True, arch=args.arch,
                       use_docker_login=args.use_docker_login)
    blocking = render_checks(checks)
    if blocking:
        sys.stderr.write('\n{} check(s) failed - nothing was done.\n'.format(len(blocking)))
        return 1
    if args.preflight_only:
        sys.stderr.write('\npreflight only: stopping here.\n')
        return 0

    # Rewrite the component file from what CI has published, then stop. Nothing
    # is signed and nothing is committed - reviewing and committing the diff is
    # still the promote.
    if args.update_components:
        path = args.components or 'release/components.json'
        try:
            with open(path) as handle:
                current, _ = build_mod.components_from_file(handle.read())
        except OSError as exc:
            sys.stderr.write('cannot read {}: {}\n'.format(path, exc))
            return 1

        resolver = registry_mod.DockerHubResolver(
            use_docker_config=args.use_docker_login)
        sys.stderr.write('\nasking the registry what CI has published '
                         '(a few calls per component)...\n\n')
        records = candidates_mod.survey(current, resolver.list_tags,
                                        resolver.labels)
        changed = candidates_mod.describe(records, sys.stderr)
        if changed:
            write_components(path, candidates_mod.apply(current, records))
            sys.stderr.write(
                '\n  git diff {}\n'
                '  git commit -am "promote ..."\n'.format(path))
        return 0

    try:
        if args.from_stable:
            sys.stderr.write('\nreading current versions from the endpoint...\n')
            tags, unresolved = tags_from_stable(
                stable_fetcher(), overrides={'vernemq': args.vernemq})
            if unresolved:
                sys.stderr.write(
                    '\nthe endpoint could not answer for:\n  {}\n'
                    'supply these with --components instead - a release must '
                    'not guess a version.\n'.format('\n  '.join(unresolved)))
                return 1
        else:
            with open(args.components) as handle:
                tags, features = build_mod.components_from_file(handle.read())
            if features:
                sys.stderr.write('note: features in the component file are '
                                 'ignored by this tool\n')

        sys.stderr.write('\ncomponent set:\n')
        describe_tags(manifest_mod.per_arch_tags(tags))

        if args.write_components:
            write_components(args.write_components,
                             manifest_mod.per_arch_tags(tags))
            return 0

        # Say where the chosen set diverges from what devices run today, so
        # pinning something older or newer is never accidental.
        if args.components:
            sys.stderr.write('\nagainst current stable:\n')
            compare_to_stable(manifest_mod.per_arch_tags(tags), stable_fetcher())

        previous_raw = None
        if args.previous and os.path.exists(args.previous):
            with open(args.previous) as handle:
                previous_raw = handle.read()

        signable, signature = cut(
            tags=tags, version_text=version_text, work_dir=args.work_dir,
            key_path=args.key, previous_raw=previous_raw, arch=args.arch,
            use_docker_login=args.use_docker_login,
            strict_provenance=args.strict_provenance or None)
    except (CutError, build_mod.BuildError, manifest_mod.ManifestError,
            prepare_mod.PrepareError, sign_mod.SignError,
            registry_mod.RegistryError) as exc:
        sys.stderr.write('\n{}\n'.format(exc))
        return 1

    document = json.loads(signable.decode('utf-8'))
    manifest_path = os.path.join(args.work_dir, 'manifest.json')

    if args.public_key:
        ok, detail = verify_as_a_device_would(
            manifest_path, manifest_path + sign_mod.SIGNATURE_SUFFIX,
            args.public_key)
        sys.stderr.write('\nverify as a device would: {}\n'.format(
            'OK - ' + detail if ok else 'FAILED'))
        if not ok:
            sys.stderr.write('  {}\n'.format(detail[:300]))
            return 1
    else:
        sys.stderr.write(
            '\nskipped local verify (pass --public-key to check the signature '
            'before publishing)\n')

    publish_block(signable, signature, document['counter'], arch=args.arch)
    sys.stderr.write('\nartifacts in {}\n'.format(args.work_dir))
    return 0


if __name__ == '__main__':
    sys.exit(main())
