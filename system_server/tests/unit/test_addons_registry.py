"""Catalog discovery.

The catalog is the enumeration release/manifest.py deliberately does not carry:
its comment on TIER_FEATURE says a hardcoded list would silently drop a new
service from the pinning. That only holds if discovery is strict - a descriptor
that is skipped rather than raised on is an addon that vanishes from the UI and
from the release without anyone being told.
"""
import json
import pathlib

import pytest

from addons import registry, schema


@pytest.fixture
def catalog(tmp_path):
    def write(name, **overrides):
        base = {
            'schema': 'flexrun.addon/v1',
            'name': name,
            'label': name.title(),
            'description': 'x',
            'tier': 'included',
            'kind': 'container',
            'arches': ['x86'],
            'component': name,
            'container': {'name': name},
            'health': {'type': 'http', 'port': 9000, 'path': '/'},
        }
        base.update(overrides)
        folder = tmp_path / base.get('_folder', name)
        folder.mkdir(parents=True, exist_ok=True)
        base.pop('_folder', None)
        (folder / 'addon.json').write_text(json.dumps(base))
        return base

    write.root = str(tmp_path)
    return write


class TestShippedCatalog:
    """The descriptors actually in the repo, not fixtures."""

    @pytest.mark.unit
    def test_every_shipped_descriptor_is_valid(self):
        addons = registry.load()
        assert set(addons) == {'anomaly_audio', 'anomaly_visual', 'assembly',
                               'client_mode', 'ftp', 'ocr', 'timemachine'}

    @pytest.mark.unit
    def test_the_release_components_are_the_container_addons(self):
        # This is what build_release.py should populate features= from.
        assert registry.components() == {
            'anomaly_audio': 'audio-anomaly',
            'anomaly_visual': 'anomaly-server',
            'assembly': 'assembly-client',
            'ocr': 'ocr',
        }

    @pytest.mark.unit
    def test_ocr_is_not_offered_on_arm(self):
        # Only an x86 image is published; offering the toggle would enqueue an
        # install that could never succeed.
        assert 'ocr' in registry.for_arch('x86')
        assert 'ocr' not in registry.for_arch('arm')

    @pytest.mark.unit
    def test_the_anomaly_family_shares_a_group(self):
        # Siblings so each is licensed and pinned on its own, grouped so the UI
        # still presents one product.
        group = {'key': 'anomaly', 'label': 'Anomaly Detection'}
        assert registry.get('anomaly_audio')['group'] == group
        assert registry.get('anomaly_visual')['group'] == group

    @pytest.mark.unit
    def test_the_anomaly_siblings_are_licensed_separately(self):
        # One entitlement for both would sell audio to anyone buying images.
        assert (registry.get('anomaly_audio')['entitlement']
                != registry.get('anomaly_visual')['entitlement'])

    @pytest.mark.unit
    def test_visual_anomaly_is_not_offered_on_arm(self):
        # build.sh publishes fvonprem/x86-anomaly-server only; offering the
        # toggle on arm would enqueue an install that can never succeed.
        assert 'anomaly_visual' in registry.for_arch('x86')
        assert 'anomaly_visual' not in registry.for_arch('arm')

    @pytest.mark.unit
    def test_every_enterprise_addon_names_an_entitlement(self):
        for addon in registry.load().values():
            if addon['tier'] == schema.TIER_ENTERPRISE:
                assert addon['entitlement'], addon['name']

    @pytest.mark.unit
    def test_no_two_addons_claim_the_same_container(self):
        names = [a['container']['name'] for a in registry.containers().values()]
        assert len(names) == len(set(names))

    @pytest.mark.unit
    def test_no_two_addons_claim_the_same_legacy_path(self):
        seen = set()
        for addon in registry.load().values():
            for path in (addon.get('legacy_routes') or {}).values():
                assert path not in seen, path
                seen.add(path)


class TestDiscovery:
    @pytest.mark.unit
    def test_a_descriptor_is_found_by_its_folder(self, catalog):
        catalog('alpha')
        assert sorted(registry.load(catalog.root)) == ['alpha']

    @pytest.mark.unit
    def test_a_folder_that_disagrees_with_the_name_is_refused(self, catalog):
        # The folder is how a person finds an addon; a mismatch means the name
        # in the release manifest points at a directory nobody can locate.
        catalog('alpha', _folder='beta')
        with pytest.raises(registry.RegistryError) as exc:
            registry.load(catalog.root)
        assert 'beta' in str(exc.value)

    @pytest.mark.unit
    def test_an_invalid_descriptor_raises_rather_than_being_skipped(self, catalog):
        catalog('alpha')
        catalog('broken', tier='premium')
        with pytest.raises(schema.AddonError):
            registry.load(catalog.root)

    @pytest.mark.unit
    def test_malformed_json_names_the_file(self, catalog):
        catalog('alpha')
        (pathlib.Path(catalog.root) / 'alpha' / 'addon.json').write_text('{not json')
        with pytest.raises(registry.RegistryError) as exc:
            registry.load(catalog.root)
        assert 'alpha' in str(exc.value)

    @pytest.mark.unit
    def test_a_folder_without_a_descriptor_is_ignored(self, catalog):
        catalog('alpha')
        (pathlib.Path(catalog.root) / 'notes').mkdir()
        assert sorted(registry.load(catalog.root)) == ['alpha']

    @pytest.mark.unit
    def test_a_missing_catalog_is_an_error(self, tmp_path):
        with pytest.raises(registry.RegistryError):
            registry.load(str(tmp_path / 'nope'))

    @pytest.mark.unit
    def test_an_unknown_name_lists_what_exists(self, catalog):
        catalog('alpha')
        with pytest.raises(registry.RegistryError) as exc:
            registry.get('missing', catalog.root)
        assert 'alpha' in str(exc.value)
