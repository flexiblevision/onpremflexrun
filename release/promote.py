"""Promote a signed release, and deploy it.

Promoting used to be three manual steps - paste the manifest into a Python
dict, change an integer, run gcloud - and the third was forgettable. Forgetting
it leaves git saying the release shipped while the endpoint 404s at every
device, which looks like an outage rather than a missed command.

This is deliberately NOT part of `cut`. Signing says "this is a genuine
release"; promoting says "the fleet should run it". Keeping them apart is what
lets you cut a release, sit on it, try it on one device through the beta
channel, and only then move stable - without re-signing anything.
"""
import argparse
import base64
import json
import os
import subprocess
import sys

CLOUDFUNCTION = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'cloudfunction')
DATA = os.path.join(CLOUDFUNCTION, 'releases.json')
WORK_DIR = '.release-work'

DEPLOY = [
    'gcloud', 'functions', 'deploy', 'release_manifest',
    '--gen2', '--runtime', 'python312', '--trigger-http',
    '--allow-unauthenticated', '--entry-point', 'release_manifest',
    '--region', 'us-central1', '--source', CLOUDFUNCTION,
    '--project', 'flexible-vision-staging',
]

PROXY = 'https://functions-proxy.flexiblevision.com/release_manifest'


class PromoteError(Exception):
    pass


def load(path=DATA):
    with open(path) as handle:
        return json.load(handle)


def save(data, path=DATA):
    with open(path, 'w') as handle:
        handle.write(json.dumps(data, indent=2, sort_keys=True) + '\n')


def read_cut(work_dir=WORK_DIR):
    """The manifest and signature a cut left behind."""
    manifest = os.path.join(work_dir, 'manifest.json')
    signature = manifest + '.sig'
    for path in (manifest, signature):
        if not os.path.isfile(path):
            raise PromoteError(
                '{} is missing - run a cut first, or pass --counter to promote '
                'a release that is already published'.format(path))
    with open(manifest, 'rb') as handle:
        raw = handle.read()
    with open(signature) as handle:
        return raw, handle.read().strip()


def add_release(data, raw, signature):
    """Add a signed release to the store. Returns (arch, counter)."""
    parsed = json.loads(raw.decode('utf-8'))
    arch = parsed.get('arch')
    counter = parsed.get('counter')
    if not arch:
        raise PromoteError('manifest has no arch - it predates per-arch releases')
    if not isinstance(counter, int):
        raise PromoteError('manifest has no usable counter')

    entries = data['releases'].setdefault(arch, {})
    key = str(counter)
    encoded = base64.b64encode(raw).decode('ascii')

    if key in entries and entries[key]['manifest_b64'] != encoded:
        raise PromoteError(
            'release {} already exists for {} with different bytes. RELEASES is '
            'append-only - a counter names one exact release, and reusing it '
            'would give two devices different software under one number.'
            .format(counter, arch))

    entries[key] = {'manifest_b64': encoded, 'signature': signature}
    return arch, counter


def point(data, arch, channel, counter):
    if str(counter) not in data['releases'].get(arch, {}):
        raise PromoteError(
            'cannot point {}/{} at release {} - it is not published for that '
            'architecture'.format(arch, channel, counter))
    data['channels'].setdefault(arch, {})[channel] = counter


def describe(data, arch, channel, counter, previous, stream=sys.stderr):
    stream.write('\n  arch      {}\n'.format(arch))
    stream.write('  channel   {}\n'.format(channel))
    stream.write('  from      {}\n'.format(
        previous if previous is not None else 'nothing promoted'))
    stream.write('  to        {}\n'.format(counter))
    stream.write('  published {}\n'.format(
        ', '.join(sorted(data['releases'].get(arch, {}), key=int)) or 'none'))


def deploy(run=None):
    runner = run or (lambda argv: subprocess.run(argv))
    result = runner(DEPLOY)
    if result.returncode != 0:
        raise PromoteError(
            'deploy failed (exit {}). releases.json is already committed-ready '
            'but the live endpoint still serves the previous release.'
            .format(result.returncode))
    return result


def confirm_live(arch, channel, counter, fetcher=None):
    """Ask the proxy what it now serves. A deploy that reports success but is
    not reachable through functions-proxy is the failure that matters, and it
    is invisible in gcloud's output."""
    if fetcher is None:
        from . import fetch as fetch_mod

        def fetcher(a, c):
            raw, _sig, envelope = fetch_mod.fetch_release(a, channel=c)
            return envelope

    try:
        envelope = fetcher(arch, channel)
    except Exception as exc:
        return False, str(exc)

    served = envelope.get('counter')
    if served != counter:
        return False, 'proxy serves counter {}, expected {}'.format(served, counter)
    return True, 'proxy serves {} on {}/{}'.format(counter, arch, channel)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Promote a signed release and deploy it.')
    parser.add_argument('--arch', help='defaults to the arch in the manifest')
    parser.add_argument('--channel', default='stable')
    parser.add_argument('--counter', type=int,
                        help='promote a release that is already published, '
                             'instead of the one a cut just produced')
    parser.add_argument('--work-dir', default=WORK_DIR)
    parser.add_argument('--yes', action='store_true',
                        help='skip the confirmation')
    parser.add_argument('--no-deploy', action='store_true',
                        help='write releases.json and stop, without deploying')
    args = parser.parse_args(argv)

    try:
        data = load()

        if args.counter is None:
            raw, signature = read_cut(args.work_dir)
            arch, counter = add_release(data, raw, signature)
        else:
            counter = args.counter
            arch = args.arch
            if not arch:
                matches = [a for a, e in data['releases'].items()
                           if str(counter) in e]
                if len(matches) != 1:
                    raise PromoteError(
                        'release {} exists for {} - name one with --arch'
                        .format(counter, matches or 'no architecture'))
                arch = matches[0]

        if args.arch and args.arch != arch:
            raise PromoteError(
                'manifest is for {} but --arch says {}'.format(arch, args.arch))

        previous = (data['channels'].get(arch) or {}).get(args.channel)
        point(data, arch, args.channel, counter)
        describe(data, arch, args.channel, counter, previous)

        if previous == counter:
            sys.stderr.write('\nalready promoted - nothing to change.\n')
            return 0

        if not args.yes:
            sys.stderr.write(
                '\nType the counter to promote it, anything else to abort: ')
            sys.stderr.flush()
            if sys.stdin.readline().strip() != str(counter):
                sys.stderr.write('aborted - nothing was written.\n')
                return 1

        save(data)
        sys.stderr.write('\nwrote {}\n'.format(DATA))

        if args.no_deploy:
            sys.stderr.write(
                'not deployed (--no-deploy). The live endpoint still serves the '
                'previous release until you deploy.\n')
            return 0

        sys.stderr.write('deploying...\n')
        deploy()

        ok, detail = confirm_live(arch, args.channel, counter)
        sys.stderr.write('\n{}: {}\n'.format('live' if ok else 'NOT LIVE', detail))
        if not ok:
            return 1

        sys.stderr.write(
            '\nCommit release/cloudfunction/releases.json - the deployed '
            'function and the repository must agree.\n')
        return 0

    except PromoteError as exc:
        sys.stderr.write('\n{}\n'.format(exc))
        return 1


if __name__ == '__main__':
    sys.exit(main())
