"""Applying a signed release on a device.

The order is the whole safety argument: nothing is fetched before we know what
this device has accepted, nothing is applied before the signature and counter
check out, and nothing is recorded before the containers actually moved.
"""
import json
import os

import pytest

import upgrade_runner
from release import manifest as m


def entry(repo, tag, ch='a'):
    return {'repository': repo, 'tag': tag, 'digest': 'sha256:' + ch * 64,
            'tier': 'foundational', 'provenance': 'built'}


def document(counter=5, arch='x86', release='1.9.5'):
    return {'schema': m.SCHEMA, 'release': release, 'counter': counter,
            'arch': arch, 'created': '2026-08-31T00:00:00Z',
            'notAfter': '2026-11-29T00:00:00Z',
            'flexrun': {'repository': 'x', 'commit': 'a' * 40},
            'images': {arch: {
                'backend': entry('fvonprem/x86-backend', '1.999', 'a'),
                'vision': entry('fvonprem/x86-vision', '1.999', 'b'),
            }}, 'notes': m.blank_notes()}


class Recorder:
    """Captures the order things happened in, which is what is being asserted."""

    def __init__(self, doc=None, run_code=0):
        self.doc = doc or document()
        self.run_code = run_code
        self.events = []
        self.state = {'high_water': 3, 'installed': None, 'history': []}
        self.recorded = None
        self.run_env = {}

    def install(self, monkeypatch, tmp_path, counter_asked=None):
        from release import apply as apply_mod
        from release import fetch as fetch_mod
        from release import state as state_mod
        from release import verify as verify_mod

        raw = json.dumps(self.doc).encode()

        def fetch_release(arch, channel='stable', counter=None, **kw):
            self.events.append('fetch')
            return raw, 'SIGNATURE==', {'arch': arch, 'counter': counter}

        def verify(raw_manifest, arch, high_water, now, **kw):
            self.events.append('verify(high_water=%s)' % high_water)
            return json.loads(raw_manifest.decode())

        def verify_rollback(raw_manifest, arch, known, now, **kw):
            self.events.append('verify_rollback')
            return json.loads(raw_manifest.decode())

        def read(collection):
            self.events.append('read_state')
            return dict(self.state)

        def record_applied(collection, parsed, now=None, rolled_back=False):
            self.events.append('record(counter=%s,rolled_back=%s)'
                               % (parsed['counter'], rolled_back))
            self.recorded = parsed
            return parsed

        def run(run_id, versions, plan_path=None):
            self.events.append('run')
            self.run_env = {'versions': versions, 'plan_path': plan_path}
            return self.run_code

        monkeypatch.setattr(fetch_mod, 'fetch_release', fetch_release)
        monkeypatch.setattr(verify_mod, 'verify', verify)
        monkeypatch.setattr(verify_mod, 'verify_rollback', verify_rollback)
        monkeypatch.setattr(state_mod, 'read', read)
        monkeypatch.setattr(state_mod, 'record_applied', record_applied)
        monkeypatch.setattr(state_mod, 'known_counters', lambda c: {1, 2, 3})
        monkeypatch.setattr(upgrade_runner, 'run', run)

    def go(self, tmp_path, **kw):
        return upgrade_runner.run_release(
            'run-1', 'x86', collection=object(),
            plan_path=str(tmp_path / 'plan'), trust_dir=str(tmp_path), **kw)


class TestOrdering:

    def test_state_is_read_before_anything_is_fetched(self, monkeypatch, tmp_path):
        """high_water has to come from the device, not from the release being
        offered - otherwise the thing being checked supplies its own answer."""
        r = Recorder()
        r.install(monkeypatch, tmp_path)
        r.go(tmp_path)
        assert r.events.index('read_state') < r.events.index('fetch')

    def test_nothing_is_applied_before_verification(self, monkeypatch, tmp_path):
        r = Recorder()
        r.install(monkeypatch, tmp_path)
        r.go(tmp_path)
        verify_at = [i for i, e in enumerate(r.events) if e.startswith('verify(')][0]
        assert verify_at < r.events.index('run')

    def test_nothing_is_recorded_before_the_containers_moved(self, monkeypatch, tmp_path):
        r = Recorder()
        r.install(monkeypatch, tmp_path)
        r.go(tmp_path)
        assert r.events.index('run') < [
            i for i, e in enumerate(r.events) if e.startswith('record(')][0]

    def test_the_high_water_mark_is_what_gets_checked(self, monkeypatch, tmp_path):
        """Not the running counter: passing that would let a device that had
        rolled back be pushed straight down again."""
        r = Recorder()
        r.state = {'high_water': 9, 'installed': {'counter': 2}, 'history': []}
        r.install(monkeypatch, tmp_path)
        r.go(tmp_path)
        assert 'verify(high_water=9)' in r.events


class TestFailureStopsShort:

    def test_a_failed_upgrade_records_nothing(self, monkeypatch, tmp_path):
        """Recording a release the containers never moved to would make the
        device refuse the retry as a rollback."""
        r = Recorder(run_code=23)
        r.install(monkeypatch, tmp_path)
        assert r.go(tmp_path) == 23
        assert r.recorded is None
        assert not [e for e in r.events if e.startswith('record(')]

    def test_a_verification_failure_never_reaches_the_containers(self, monkeypatch, tmp_path):
        from release import verify as verify_mod

        def boom(*a, **kw):
            raise verify_mod.VerificationError('signature did not match')

        r = Recorder()
        r.install(monkeypatch, tmp_path)
        monkeypatch.setattr(verify_mod, 'verify', boom)
        with pytest.raises(verify_mod.VerificationError):
            r.go(tmp_path)
        assert 'run' not in r.events

    def test_an_unreachable_endpoint_never_reaches_the_containers(self, monkeypatch, tmp_path):
        from release import fetch as fetch_mod

        def boom(*a, **kw):
            raise fetch_mod.FetchError('offline')

        r = Recorder()
        r.install(monkeypatch, tmp_path)
        monkeypatch.setattr(fetch_mod, 'fetch_release', boom)
        with pytest.raises(fetch_mod.FetchError):
            r.go(tmp_path)
        assert 'run' not in r.events


class TestRollback:

    def test_asking_for_a_counter_uses_the_rollback_check(self, monkeypatch, tmp_path):
        """An explicit counter is deliberately older, so the high-water rule
        must not apply - but it still has to be one this device has run."""
        r = Recorder(doc=document(counter=2))
        r.install(monkeypatch, tmp_path)
        r.go(tmp_path, counter=2)
        assert 'verify_rollback' in r.events
        assert not [e for e in r.events if e.startswith('verify(')]

    def test_a_rollback_is_recorded_as_one(self, monkeypatch, tmp_path):
        """state.record_applied keeps high_water where it was for a rollback;
        recording it as a normal install would strand the device below it."""
        r = Recorder(doc=document(counter=2))
        r.install(monkeypatch, tmp_path)
        r.go(tmp_path, counter=2)
        assert 'record(counter=2,rolled_back=True)' in r.events


class TestWhatReachesTheDeployScripts:

    def test_the_plan_path_is_handed_to_the_scripts(self, monkeypatch, tmp_path):
        r = Recorder()
        r.install(monkeypatch, tmp_path)
        r.go(tmp_path)
        assert r.run_env['plan_path'] == str(tmp_path / 'plan')

    def test_the_plan_file_pins_digests(self, monkeypatch, tmp_path):
        r = Recorder()
        r.install(monkeypatch, tmp_path)
        r.go(tmp_path)
        body = (tmp_path / 'plan').read_text()
        assert '@sha256:' in body
        assert 'backend 1.999 fvonprem/x86-backend@sha256:' in body

    def test_versions_are_positional_for_the_legacy_scripts(self, monkeypatch, tmp_path):
        r = Recorder()
        r.install(monkeypatch, tmp_path)
        r.go(tmp_path)
        assert len(r.run_env['versions']) == len(upgrade_runner.VERSION_ARGS)


class TestPreferManifestWithFallback:
    """A device must never be stranded because the release endpoint is down -
    but the fallback must not become a way around verification."""

    def _legacy(self, monkeypatch, calls):
        monkeypatch.setattr(upgrade_runner, '_legacy_versions',
                            lambda: ['1.97'] * 7)
        monkeypatch.setattr(upgrade_runner, '_device_arch', lambda: 'x86')

        def run(run_id, versions, plan_path=None):
            calls.append(('legacy' if plan_path is None else 'release', versions))
            return 0
        monkeypatch.setattr(upgrade_runner, 'run', run)

    def test_an_unreachable_endpoint_falls_back(self, monkeypatch):
        from release import fetch as fetch_mod
        calls = []
        self._legacy(monkeypatch, calls)
        monkeypatch.setattr(upgrade_runner, 'run_release',
                            lambda *a, **k: (_ for _ in ()).throw(
                                fetch_mod.FetchError('offline')))
        assert upgrade_runner._release_or_legacy('r1') == 0
        assert calls == [('legacy', ['1.97'] * 7)]

    def test_nothing_promoted_falls_back(self, monkeypatch):
        from release import fetch as fetch_mod
        calls = []
        self._legacy(monkeypatch, calls)
        monkeypatch.setattr(upgrade_runner, 'run_release',
                            lambda *a, **k: (_ for _ in ()).throw(
                                fetch_mod.FetchError('no release promoted')))
        upgrade_runner._release_or_legacy('r1')
        assert calls and calls[0][0] == 'legacy'

    def test_a_verification_failure_does_NOT_fall_back(self, monkeypatch):
        """The critical one. Falling back here would mean a tampered or
        wrongly-signed manifest downgrades the device to an unverified upgrade
        of the same thing - making the signature advisory."""
        from release import verify as verify_mod
        calls = []
        self._legacy(monkeypatch, calls)
        monkeypatch.setattr(upgrade_runner, 'run_release',
                            lambda *a, **k: (_ for _ in ()).throw(
                                verify_mod.VerificationError('bad signature')))
        with pytest.raises(verify_mod.VerificationError):
            upgrade_runner._release_or_legacy('r1')
        assert calls == []

    def test_a_usable_release_is_preferred(self, monkeypatch):
        calls = []
        self._legacy(monkeypatch, calls)
        monkeypatch.setattr(upgrade_runner, 'run_release',
                            lambda *a, **k: calls.append(('release', None)) or 0)
        upgrade_runner._release_or_legacy('r1')
        assert calls == [('release', None)]


class TestUnchangedContainersAreLeftAlone:
    """A release pinning what a device already runs must be a no-op, not a
    full restart. This is the case for a baseline release seeded from
    latest_stable_version, where by definition nothing has moved."""

    def test_running_versions_are_consulted_by_default(self, monkeypatch, tmp_path):
        r = Recorder()
        r.install(monkeypatch, tmp_path)
        monkeypatch.setattr(upgrade_runner, '_running_versions',
                            lambda: {'backend': '1.999', 'vision': '1.999'})
        r.go(tmp_path)
        assert r.run_env['versions'] == ['True'] * 7, \
            'a release pinning what is already running restarted containers'

    def test_a_real_change_is_still_applied(self, monkeypatch, tmp_path):
        r = Recorder()
        r.install(monkeypatch, tmp_path)
        monkeypatch.setattr(upgrade_runner, '_running_versions',
                            lambda: {'backend': '1.97', 'vision': '1.999'})
        r.go(tmp_path)
        order = upgrade_runner.VERSION_ARGS
        assert r.run_env['versions'][order.index('backend')] == '1.999'
        assert r.run_env['versions'][order.index('vision')] == 'True'

    def test_an_uninspectable_container_is_upgraded_not_skipped(self, monkeypatch, tmp_path):
        """Not knowing what a container runs must not be read as 'it is fine'."""
        r = Recorder()
        r.install(monkeypatch, tmp_path)
        monkeypatch.setattr(upgrade_runner, '_running_versions', lambda: {})
        r.go(tmp_path)
        order = upgrade_runner.VERSION_ARGS
        assert r.run_env['versions'][order.index('backend')] == '1.999'
