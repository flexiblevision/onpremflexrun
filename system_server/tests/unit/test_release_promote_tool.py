"""Promoting a signed release.

The step that used to be three manual actions with a forgettable third. These
are mostly about the ways an automated promote could ship the wrong thing
faster than a human would have.
"""
import base64
import json

import pytest

from release import promote as p


def manifest_bytes(counter=1, arch='x86', release='1.9.1'):
    return json.dumps({'counter': counter, 'arch': arch, 'release': release,
                       'images': {arch: {}}}, sort_keys=True).encode()


def store(**overrides):
    data = {'releases': {'x86': {}, 'arm': {}},
            'channels': {'x86': {'stable': None, 'beta': None},
                         'arm': {'stable': None, 'beta': None}}}
    data.update(overrides)
    return data


class TestAddRelease:

    def test_it_files_under_the_arch_and_counter_from_the_manifest(self):
        data = store()
        arch, counter = p.add_release(data, manifest_bytes(7, 'arm'), 'SIG')
        assert (arch, counter) == ('arm', 7)
        assert '7' in data['releases']['arm']
        assert data['releases']['x86'] == {}

    def test_the_stored_bytes_round_trip_exactly(self):
        """The signature covers these bytes. Re-encoding or reformatting them
        makes every device fail verification."""
        raw = manifest_bytes()
        data = store()
        p.add_release(data, raw, 'SIG')
        stored = data['releases']['x86']['1']['manifest_b64']
        assert base64.b64decode(stored) == raw

    def test_re_promoting_identical_bytes_is_fine(self):
        """Re-running after a failed deploy must not be an error."""
        data = store()
        raw = manifest_bytes()
        p.add_release(data, raw, 'SIG')
        p.add_release(data, raw, 'SIG')
        assert len(data['releases']['x86']) == 1

    def test_reusing_a_counter_for_different_bytes_is_refused(self):
        """Append-only. Two devices on 'release 1' running different software
        is the failure the counter exists to prevent."""
        data = store()
        p.add_release(data, manifest_bytes(1, release='1.9.1'), 'SIG')
        with pytest.raises(p.PromoteError, match='append-only'):
            p.add_release(data, manifest_bytes(1, release='1.9.9'), 'SIG')

    def test_a_manifest_without_an_arch_is_refused(self):
        raw = json.dumps({'counter': 1, 'release': '1.9.1'}).encode()
        with pytest.raises(p.PromoteError, match='no arch'):
            p.add_release(store(), raw, 'SIG')


class TestPointing:

    def test_a_channel_can_only_point_at_something_published(self):
        """The exact failure the consistency test catches after the fact -
        caught here before it is written instead."""
        with pytest.raises(p.PromoteError, match='not published'):
            p.point(store(), 'x86', 'stable', 5)

    def test_promoting_one_arch_leaves_the_other_alone(self):
        data = store()
        p.add_release(data, manifest_bytes(1, 'x86'), 'SIG')
        p.point(data, 'x86', 'stable', 1)
        assert data['channels']['arm']['stable'] is None

    def test_promoting_one_channel_leaves_the_other_alone(self):
        """Trying a release on beta must not move stable underneath the fleet."""
        data = store()
        p.add_release(data, manifest_bytes(1), 'SIG')
        p.point(data, 'x86', 'beta', 1)
        assert data['channels']['x86']['stable'] is None
        assert data['channels']['x86']['beta'] == 1

    def test_a_rollback_is_just_pointing_at_an_older_release(self):
        data = store()
        p.add_release(data, manifest_bytes(1), 'SIG')
        p.add_release(data, manifest_bytes(2), 'SIG')
        p.point(data, 'x86', 'stable', 2)
        p.point(data, 'x86', 'stable', 1)
        assert data['channels']['x86']['stable'] == 1
        assert sorted(data['releases']['x86']) == ['1', '2']


class TestDeployFailureIsLoud:

    def test_a_failed_deploy_raises_rather_than_reporting_success(self):
        class Result:
            returncode = 1
        with pytest.raises(p.PromoteError, match='deploy failed'):
            p.deploy(run=lambda argv: Result())

    def test_the_error_says_the_endpoint_is_unchanged(self):
        class Result:
            returncode = 1
        with pytest.raises(p.PromoteError, match='still serves the previous'):
            p.deploy(run=lambda argv: Result())

    def test_the_deploy_targets_the_cloudfunction_directory(self):
        seen = {}

        class Result:
            returncode = 0

        def run(argv):
            seen['argv'] = argv
            return Result()
        p.deploy(run=run)
        assert '--source' in seen['argv']
        assert seen['argv'][seen['argv'].index('--source') + 1].endswith(
            'release/cloudfunction')


class TestConfirmLive:
    """gcloud reporting success is not the same as devices being able to reach
    it: the proxy is the only route they have."""

    def test_it_confirms_what_the_proxy_actually_serves(self):
        ok, detail = p.confirm_live('x86', 'stable', 4,
                                    fetcher=lambda a, c: {'counter': 4})
        assert ok and 'serves 4' in detail

    def test_a_stale_endpoint_is_caught(self):
        """The deploy succeeded but the proxy still has the old revision."""
        ok, detail = p.confirm_live('x86', 'stable', 4,
                                    fetcher=lambda a, c: {'counter': 3})
        assert not ok and 'expected 4' in detail

    def test_an_unreachable_proxy_is_not_success(self):
        def boom(a, c):
            raise RuntimeError('name resolution failed')
        ok, detail = p.confirm_live('x86', 'stable', 4, fetcher=boom)
        assert not ok and 'name resolution' in detail
