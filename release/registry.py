"""Resolve a Docker Hub tag to its immutable manifest digest.

The digest is discovered from the registry rather than reported by whatever
built the image, so this works regardless of where or how the images are built.
That matters: the fvonprem images are built outside this repository.

The authoritative value is the Docker-Content-Digest response header on a
manifest request, which is the digest the registry itself will resolve that tag
to. Computing a hash locally would be a different number and would not pin
anything.
"""
import base64
import binascii
import json
import os
import re

import requests

AUTH_URL = 'https://auth.docker.io/token'
REGISTRY_URL = 'https://registry-1.docker.io'

# A tag may point at a manifest list (multi-arch) or a single manifest. Ask for
# both, plus the OCI equivalents, or the registry may hand back a converted
# v1 manifest whose digest differs from the one a client would pull by.
ACCEPT = ', '.join([
    'application/vnd.docker.distribution.manifest.list.v2+json',
    'application/vnd.docker.distribution.manifest.v2+json',
    'application/vnd.oci.image.index.v1+json',
    'application/vnd.oci.image.manifest.v1+json',
])

TIMEOUT = 30


class RegistryError(Exception):
    pass


DOCKER_CONFIG = '~/.docker/config.json'
_HUB_KEYS = ('https://index.docker.io/v1/', 'index.docker.io',
             'registry-1.docker.io')


def docker_config_credentials(path=None):
    """Credentials from an existing `docker login`, or (None, None).

    Opt-in only - see DockerHubResolver. Returns nothing when the credential
    lives in an external helper (credsStore/credHelpers), because then it is not
    in the file to read.
    """
    location = os.path.expanduser(path or DOCKER_CONFIG)
    try:
        with open(location) as handle:
            config = json.load(handle)
    except (OSError, ValueError):
        return None, None

    auths = config.get('auths') or {}
    for key in _HUB_KEYS:
        entry = auths.get(key) or {}
        if entry.get('username') and entry.get('password'):
            return entry['username'], entry['password']
        encoded = entry.get('auth')
        if encoded:
            try:
                decoded = base64.b64decode(encoded).decode('utf-8')
            except (ValueError, binascii.Error, UnicodeDecodeError):
                continue
            user, sep, secret = decoded.partition(':')
            if sep and user and secret:
                return user, secret
    return None, None


class DockerHubResolver:
    """Callable resolver: resolver(repository, tag) -> 'sha256:...'

    Credentials come from the environment by default and never from a config
    file, so a CI run has to be given them explicitly and cannot fall back to
    something that happens to be lying around.

    use_docker_config=True opts into an existing `docker login` for a local run.
    Deliberately not the default: in CI a silent fallback turns a missing secret
    into a confusing digest mismatch instead of a clear failure.
    """

    def __init__(self, username=None, password=None, session=None,
                 use_docker_config=False, config_path=None):
        self.username = username if username is not None else os.environ.get('DOCKERHUB_USERNAME')
        self.password = password if password is not None else os.environ.get('DOCKERHUB_TOKEN')

        if use_docker_config and not (self.username and self.password):
            self.username, self.password = docker_config_credentials(config_path)

        self.session = session or requests.Session()
        self._token_cache = {}

    def _token(self, repository):
        if repository in self._token_cache:
            return self._token_cache[repository]

        params = {
            'service': 'registry.docker.io',
            'scope': 'repository:{}:pull'.format(repository),
        }
        auth = (self.username, self.password) if self.username and self.password else None
        response = self.session.get(AUTH_URL, params=params, auth=auth, timeout=TIMEOUT)
        if response.status_code != 200:
            raise RegistryError(
                'could not get a pull token for {} (HTTP {}). For a private '
                'repository set DOCKERHUB_USERNAME and DOCKERHUB_TOKEN.'
                .format(repository, response.status_code))
        token = response.json().get('token')
        if not token:
            raise RegistryError('auth response for {} had no token'.format(repository))
        self._token_cache[repository] = token
        return token

    def __call__(self, repository, tag):
        token = self._token(repository)
        url = '{}/v2/{}/manifests/{}'.format(REGISTRY_URL, repository, tag)
        headers = {'Accept': ACCEPT, 'Authorization': 'Bearer ' + token}

        # HEAD is enough and avoids pulling the manifest body.
        response = self.session.head(url, headers=headers, timeout=TIMEOUT)
        if response.status_code == 404:
            raise RegistryError(
                '{}:{} does not exist in the registry - a release cannot pin '
                'an image that was never pushed'.format(repository, tag))
        if response.status_code != 200:
            raise RegistryError(
                'manifest request for {}:{} returned HTTP {}'
                .format(repository, tag, response.status_code))

        digest = response.headers.get('Docker-Content-Digest')
        if not digest:
            raise RegistryError(
                'registry did not return Docker-Content-Digest for {}:{}; '
                'without it there is no authoritative digest to pin'
                .format(repository, tag))
        return digest.strip()

    def list_tags(self, repository):
        """Every tag on a repository. Paginated; the registry caps page size."""
        token = self._token(repository)
        headers = {'Authorization': 'Bearer ' + token}
        url = '{}/v2/{}/tags/list?n=1000'.format(REGISTRY_URL, repository)
        tags = []

        # Follow RFC 5988 Link headers rather than guessing page numbers - a
        # repository with hundreds of commit tags will paginate.
        while url:
            response = self.session.get(url, headers=headers, timeout=TIMEOUT)
            if response.status_code == 404:
                raise RegistryError(
                    '{} does not exist in the registry'.format(repository))
            if response.status_code != 200:
                raise RegistryError(
                    'tag listing for {} returned HTTP {}'
                    .format(repository, response.status_code))
            tags.extend(response.json().get('tags') or [])

            link = response.headers.get('Link', '')
            match = re.search(r'<([^>]+)>;\s*rel="next"', link)
            url = REGISTRY_URL + match.group(1) if match else None
        return tags

    def labels(self, repository, tag):
        """OCI labels on an image, or {} if it carries none.

        Two more round trips than digest(): the labels live in the config blob,
        which the manifest only points at. Worth it - this is the only link from
        a digest back to the commit that produced it, and without it a release
        can say exactly which bytes ship but nothing about where they came from.

        Never raises for a missing or unreadable config. An image with no labels
        is a fact for the caller to judge, not an error: every image published
        before the build scripts started stamping them is in that state.
        """
        token = self._token(repository)
        headers = {'Accept': ACCEPT, 'Authorization': 'Bearer ' + token}
        url = '{}/v2/{}/manifests/{}'.format(REGISTRY_URL, repository, tag)

        response = self.session.get(url, headers=headers, timeout=TIMEOUT)
        if response.status_code != 200:
            return {}
        try:
            manifest = response.json()
        except ValueError:
            return {}

        # A manifest list has no config of its own - the labels live on each
        # per-arch manifest it points at. These repositories publish one
        # manifest per arch under separate names, so this is not hit today.
        config = (manifest.get('config') or {}).get('digest')
        if not config:
            return {}

        blob = self.session.get(
            '{}/v2/{}/blobs/{}'.format(REGISTRY_URL, repository, config),
            headers=headers, timeout=TIMEOUT)
        if blob.status_code != 200:
            return {}
        try:
            return ((blob.json().get('config') or {}).get('Labels')) or {}
        except ValueError:
            return {}


class StaticResolver:
    """Resolver backed by a dict, for dry runs and tests."""

    def __init__(self, mapping):
        self.mapping = dict(mapping)
        self.calls = []

    def __call__(self, repository, tag):
        self.calls.append((repository, tag))
        key = '{}:{}'.format(repository, tag)
        if key not in self.mapping:
            raise RegistryError('no digest recorded for {}'.format(key))
        return self.mapping[key]
