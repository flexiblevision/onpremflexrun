"""Enabled intent, translated into what a release manifest actually pins.

state.enabled() returns addon names and release/manifest.py:applicable() matches
component keys. Those are different strings, so the join between them is a real
step and not a rename - if it is skipped, every device with an addon enabled
raises on upgrade instead of upgrading.
"""
import pytest

from addons import state
from release import manifest as m
from system_server.tests.unit.test_release_manifest import (
    COMMIT, NOW, all_tags, fake_resolver)


class TestEnabledComponents:

    def test_a_name_is_translated_to_its_component(self, monkeypatch):
        monkeypatch.setattr(state, 'enabled', lambda: ['anomaly_visual'])
        assert state.enabled_components() == ['anomaly-server']

    def test_hyphenless_names_could_never_equal_their_components(self):
        """NAME_RE forbids the hyphen every one of these components uses, so
        passing names to applicable() cannot work by accident."""
        from addons import registry, schema
        for name, component in registry.components().items():
            assert schema.NAME_RE.match(name), name
            if '-' in component:
                assert name != component

    def test_a_stale_record_is_dropped_not_reported(self, monkeypatch):
        """An addon this build no longer ships must not block every upgrade."""
        monkeypatch.setattr(state, 'enabled', lambda: ['anomaly_visual', 'gone_addon'])
        assert state.enabled_components() == ['anomaly-server']

    def test_nothing_enabled_is_empty_not_everything(self, monkeypatch):
        monkeypatch.setattr(state, 'enabled', lambda: [])
        assert state.enabled_components() == []


class TestAgainstApplicable:
    """The whole point of the translation."""

    def _built(self, features):
        return m.build_manifest(
            release='9.9.9', counter=1, tags=all_tags(), flexrun_commit=COMMIT,
            resolver=fake_resolver, now=NOW, features=features)

    def test_components_are_accepted(self):
        built = self._built({'anomaly-server': '1'})
        assert 'anomaly-server' in m.applicable(built, 'x86', ['anomaly-server'])

    def test_passing_the_addon_name_instead_raises(self):
        """The regression this translation exists to prevent."""
        built = self._built({'anomaly-server': '1'})
        with pytest.raises(m.ManifestError, match='does not pin enabled feature'):
            m.applicable(built, 'x86', ['anomaly_visual'])

    def test_translation_makes_the_round_trip_work(self, monkeypatch):
        monkeypatch.setattr(state, 'enabled', lambda: ['anomaly_visual'])
        built = self._built({'anomaly-server': '1'})
        applied = m.applicable(built, 'x86', state.enabled_components())
        assert 'anomaly-server' in applied
