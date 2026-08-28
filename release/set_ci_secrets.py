#!/usr/bin/env python3
"""Write DOCKERHUB_USERNAME / DOCKERHUB_TOKEN into GitHub Actions secrets.

GitHub Actions secrets are the correct home for a registry push token: they are
encrypted at rest, masked in job logs, withheld from fork pull requests, and
revocable without touching anything else. This writes them there over the API.

The token is encrypted here, on this machine, with the repository's own public
key, using libsodium's sealed box - which is what the API requires. GitHub never
receives the plaintext, and neither does any intermediary.

    python3 -m release.set_ci_secrets --repos          # per-repo secrets
    python3 -m release.set_ci_secrets --org            # one org secret, scoped
    python3 -m release.set_ci_secrets --repos --check  # report, write nothing

HOW THE VALUES ARE READ, and why it matters:

  The Docker Hub token is only ever read from an interactive no-echo prompt, or
  piped on stdin. Deliberately NOT from a command-line argument: argv is visible
  to every process on the box via /proc and ps, and lands in shell history. It
  is held in memory, sent once, and never written to disk or printed.

  The GitHub token (a different credential - this one authorises writing the
  secret) is read from GH_TOKEN or GITHUB_TOKEN if set, otherwise prompted for
  the same way.

SCOPES:

  GitHub token   classic: `repo`  (org mode also needs `admin:org`)
                 fine-grained: Secrets = Read and write, on these repositories
  Docker Hub PAT Read & Write. NOT Delete - a compromised delete-scoped token
                 could remove images the fleet still pulls by tag, which is an
                 outage with no recovery path.
"""
import argparse
import base64
import ctypes
import ctypes.util
import getpass
import json
import os
import sys

import requests

API = 'https://api.github.com'
ORG = 'flexiblevision'
TIMEOUT = 30

REPOS = ('visionapi', 'onprembackend', 'onpremfrontend',
         'predictlite', 'visiontools', 'nodecreator')

USERNAME_SECRET = 'DOCKERHUB_USERNAME'
TOKEN_SECRET = 'DOCKERHUB_TOKEN'


class SecretError(Exception):
    pass


# --- sealed box -------------------------------------------------------------

def _sodium():
    """libsodium via ctypes, or PyNaCl, whichever is present.

    ctypes first because libsodium is already on most machines, and asking
    someone to pip install a crypto library before they can rotate a credential
    is how the rotation stops happening.
    """
    name = ctypes.util.find_library('sodium')
    if name:
        try:
            lib = ctypes.CDLL(name)
            if lib.sodium_init() < 0:
                raise SecretError('sodium_init failed')
            return lib
        except OSError:
            pass
    return None


def seal(public_key_b64, plaintext):
    """Encrypt for a GitHub Actions public key (libsodium sealed box)."""
    key = base64.b64decode(public_key_b64)
    if len(key) != 32:
        raise SecretError('public key is {} bytes, expected 32'.format(len(key)))
    message = plaintext.encode('utf-8')

    lib = _sodium()
    if lib is not None:
        out = ctypes.create_string_buffer(len(message) + lib.crypto_box_sealbytes())
        rc = lib.crypto_box_seal(out, message, ctypes.c_ulonglong(len(message)), key)
        if rc != 0:
            raise SecretError('crypto_box_seal failed')
        return base64.b64encode(out.raw).decode('ascii')

    try:
        from nacl import encoding, public
    except ImportError:
        raise SecretError(
            'no libsodium and no PyNaCl - install one:\n'
            '  apt-get install libsodium23    (or)    pip install pynacl')
    box = public.SealedBox(public.PublicKey(key, encoding.RawEncoder()))
    return base64.b64encode(box.encrypt(message)).decode('ascii')


# --- github -----------------------------------------------------------------

def _session(token):
    s = requests.Session()
    s.headers.update({
        'Authorization': 'Bearer ' + token,
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
    })
    return s


def _check(response, what):
    if response.status_code in (200, 201, 204):
        return
    if response.status_code == 401:
        raise SecretError('{}: unauthorised - the GitHub token is invalid or '
                          'expired'.format(what))
    if response.status_code == 403:
        raise SecretError('{}: forbidden - the GitHub token needs `repo` scope '
                          '(and `admin:org` for org secrets)'.format(what))
    if response.status_code == 404:
        raise SecretError('{}: not found - check the name, and that the token '
                          'can see private repositories'.format(what))
    raise SecretError('{}: HTTP {} {}'.format(
        what, response.status_code, (response.text or '')[:200]))


def repo_public_key(session, repo):
    url = '{}/repos/{}/{}/actions/secrets/public-key'.format(API, ORG, repo)
    response = session.get(url, timeout=TIMEOUT)
    _check(response, 'public key for ' + repo)
    body = response.json()
    return body['key_id'], body['key']


def org_public_key(session):
    url = '{}/orgs/{}/actions/secrets/public-key'.format(API, ORG)
    response = session.get(url, timeout=TIMEOUT)
    # GitHub returns 404, not 403, when a token cannot see an org endpoint - it
    # hides existence rather than confirming it. So the generic "check the name"
    # message is actively misleading here: the name is almost always fine and
    # the token simply lacks org permission.
    if response.status_code == 404:
        raise SecretError(
            'cannot read org secrets for "{}". GitHub returns 404 rather than\n'
            '403 when a token lacks organisation permission, so this is most\n'
            'likely the token, not the name.\n\n'
            'Easiest fix - use per-repository secrets instead, which need only\n'
            'repo-level access:\n'
            '    python3 -m release.set_ci_secrets --repos\n\n'
            'Or to keep one org secret, the token needs `admin:org` (classic)\n'
            'or organisation Secrets: Read and write (fine-grained).'
            .format(ORG))
    _check(response, 'org public key')
    body = response.json()
    return body['key_id'], body['key']


def put_repo_secret(session, repo, name, value, key_id, key):
    url = '{}/repos/{}/{}/actions/secrets/{}'.format(API, ORG, repo, name)
    response = session.put(url, timeout=TIMEOUT, json={
        'encrypted_value': seal(key, value), 'key_id': key_id})
    _check(response, '{} on {}'.format(name, repo))
    return response.status_code


def repo_ids(session, repos):
    ids = []
    for repo in repos:
        response = session.get('{}/repos/{}/{}'.format(API, ORG, repo),
                               timeout=TIMEOUT)
        _check(response, 'lookup ' + repo)
        ids.append(response.json()['id'])
    return ids


def put_org_secret(session, name, value, key_id, key, selected_ids):
    """Org-level, visible only to the repositories named.

    'selected' rather than 'all': a push token that every repository in the org
    can read is a much larger blast radius than the six that need it, and org
    membership changes over time without anyone revisiting this.
    """
    url = '{}/orgs/{}/actions/secrets/{}'.format(API, ORG, name)
    response = session.put(url, timeout=TIMEOUT, json={
        'encrypted_value': seal(key, value),
        'key_id': key_id,
        'visibility': 'selected',
        'selected_repository_ids': selected_ids,
    })
    _check(response, name + ' on the org')
    return response.status_code


def existing(session, repo):
    url = '{}/repos/{}/{}/actions/secrets'.format(API, ORG, repo)
    response = session.get(url, timeout=TIMEOUT)
    _check(response, 'list secrets on ' + repo)
    return {s['name']: s['updated_at'] for s in response.json().get('secrets', [])}


# --- input ------------------------------------------------------------------

def read_secret(prompt, env_names=()):
    """From the environment, else a no-echo prompt, else piped stdin.

    Never from argv - that is readable by any process on the machine through
    /proc and ps, and is kept in shell history.
    """
    for name in env_names:
        value = os.environ.get(name)
        if value:
            return value.strip(), name
    if not sys.stdin.isatty():
        return sys.stdin.readline().strip(), 'stdin'
    return getpass.getpass(prompt).strip(), 'prompt'


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Write Docker Hub credentials into GitHub Actions secrets.')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--repos', action='store_true',
                      help='write the secrets into each repository')
    mode.add_argument('--org', action='store_true',
                      help='write one org secret, visible only to those repos')
    parser.add_argument('--only', nargs='+', metavar='REPO', default=list(REPOS),
                        help='limit to these repositories')
    parser.add_argument('--check', action='store_true',
                        help='report what is set today and write nothing')
    parser.add_argument('--username', help='Docker Hub account name (not secret)')
    args = parser.parse_args(argv)

    unknown = [r for r in args.only if r not in REPOS]
    if unknown:
        sys.stderr.write('unknown repo(s): {}\nknown: {}\n'.format(
            ', '.join(unknown), ', '.join(REPOS)))
        return 2

    gh_token, gh_source = read_secret(
        'GitHub token (repo scope, input hidden): ', ('GH_TOKEN', 'GITHUB_TOKEN'))
    if not gh_token:
        sys.stderr.write('no GitHub token given\n')
        return 2
    session = _session(gh_token)
    sys.stderr.write('GitHub token from {}\n'.format(gh_source))

    try:
        if args.check:
            sys.stderr.write('\nsecrets currently set:\n')
            for repo in args.only:
                have = existing(session, repo)
                marks = []
                for name in (USERNAME_SECRET, TOKEN_SECRET):
                    marks.append('{} {}'.format(
                        name, have[name][:10] if name in have else 'NOT SET'))
                sys.stderr.write('  {:<16} {}\n'.format(repo, '   '.join(marks)))
            sys.stderr.write('\nnothing was written (--check).\n')
            return 0

        username = args.username or input('Docker Hub username: ').strip()
        if not username:
            sys.stderr.write('no Docker Hub username given\n')
            return 2

        token, token_source = read_secret(
            'Docker Hub PAT (Read & Write, input hidden): ', ('DOCKERHUB_TOKEN',))
        if not token:
            sys.stderr.write('no Docker Hub token given\n')
            return 2
        if token_source == 'DOCKERHUB_TOKEN':
            sys.stderr.write(
                'note: token read from $DOCKERHUB_TOKEN. If you exported it at a '
                'shell prompt it is in your history - `unset DOCKERHUB_TOKEN` and '
                'clear that line when you are done.\n')

        pairs = ((USERNAME_SECRET, username), (TOKEN_SECRET, token))

        if args.org:
            key_id, key = org_public_key(session)
            ids = repo_ids(session, args.only)
            sys.stderr.write('\nwriting org secrets, visible to {} repo(s)\n'
                             .format(len(ids)))
            for name, value in pairs:
                put_org_secret(session, name, value, key_id, key, ids)
                sys.stderr.write('  set {}\n'.format(name))
        else:
            sys.stderr.write('\nwriting per-repository secrets\n')
            for repo in args.only:
                key_id, key = repo_public_key(session, repo)
                for name, value in pairs:
                    put_repo_secret(session, repo, name, value, key_id, key)
                sys.stderr.write('  {:<16} {} + {}\n'.format(
                    repo, USERNAME_SECRET, TOKEN_SECRET))
    except SecretError as exc:
        sys.stderr.write('\n{}\n'.format(exc))
        return 1
    except requests.RequestException as exc:
        sys.stderr.write('\nnetwork error talking to GitHub: {}\n'.format(exc))
        return 1

    sys.stderr.write(
        '\nDone. The image job still only runs on master, so nothing builds\n'
        'until the workflow is merged there. Verify with:\n'
        '    python3 -m release.set_ci_secrets --repos --check\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
