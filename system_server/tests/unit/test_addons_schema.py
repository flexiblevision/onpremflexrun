"""Descriptor validation.

A descriptor is the whole contract - a typo in one is an addon that installs
nothing, or worse, an enterprise addon that reads as licensed. Validation runs
at load rather than at enable time so a bad file cannot reach a device toggle.
"""
import pytest

from addons import schema


def descriptor(**overrides):
    base = {
        'schema': 'flexrun.addon/v1',
        'name': 'demo',
        'label': 'Demo',
        'description': 'A demo addon.',
        'tier': 'enterprise',
        'entitlement': 'demo_service',
        'kind': 'container',
        'arches': ['x86'],
        'component': 'demo',
        'container': {'name': 'demo'},
        'health': {'type': 'http', 'port': 9000, 'path': '/'},
    }
    base.update(overrides)
    return base


class TestValidDescriptors:
    @pytest.mark.unit
    def test_a_complete_descriptor_passes(self):
        assert schema.validate(descriptor()) is not None

    @pytest.mark.unit
    def test_an_included_addon_needs_no_entitlement(self):
        schema.validate(descriptor(tier='included', entitlement=None))

    @pytest.mark.unit
    def test_a_non_container_addon_uses_hooks_instead(self):
        schema.validate(descriptor(
            kind='host_service', hooks='hooks.py',
            component=None, container=None,
            health={'type': 'systemd', 'unit': 'demo'}))


class TestLicensing:
    @pytest.mark.unit
    def test_an_enterprise_addon_must_name_its_entitlement(self):
        # Without one the licence check has nothing to ask the cloud about and
        # would pass by default - which is how validate_account() behaves today.
        with pytest.raises(schema.AddonError) as exc:
            schema.validate(descriptor(entitlement=None))
        assert 'entitlement' in str(exc.value)

    @pytest.mark.unit
    def test_an_included_addon_must_not_declare_one(self):
        # It would read as licensed in review while never being checked.
        with pytest.raises(schema.AddonError):
            schema.validate(descriptor(tier='included'))

    @pytest.mark.unit
    def test_an_unknown_tier_is_refused(self):
        with pytest.raises(schema.AddonError):
            schema.validate(descriptor(tier='premium'))


class TestNaming:
    @pytest.mark.unit
    @pytest.mark.parametrize('name', ['Bad-Name', 'has space', 'x', '', '9lives'])
    def test_a_name_that_is_not_a_safe_key_is_refused(self, name):
        # The name is a release feature key, a mongo key and a URL segment.
        with pytest.raises(schema.AddonError):
            schema.validate(descriptor(name=name))

    @pytest.mark.unit
    def test_a_group_needs_a_key_and_a_label(self):
        schema.validate(descriptor(group={'key': 'anomaly', 'label': 'Anomaly'}))
        with pytest.raises(schema.AddonError):
            schema.validate(descriptor(group={'key': 'anomaly'}))


class TestContainerBlock:
    @pytest.mark.unit
    def test_a_container_addon_must_name_its_release_component(self):
        with pytest.raises(schema.AddonError) as exc:
            schema.validate(descriptor(component=None))
        assert 'component' in str(exc.value)

    @pytest.mark.unit
    def test_host_networking_with_published_ports_is_refused(self):
        # -p is ignored under host networking, so the ports would be a lie.
        with pytest.raises(schema.AddonError) as exc:
            schema.validate(descriptor(container={
                'name': 'demo', 'network': 'host',
                'ports': [{'host': 1, 'container': 1}]}))
        assert 'host' in str(exc.value)

    @pytest.mark.unit
    def test_a_relative_volume_path_is_refused(self):
        # Relative to the server's cwd is relative to nothing predictable.
        with pytest.raises(schema.AddonError):
            schema.validate(descriptor(container={
                'name': 'demo',
                'volumes': [{'host': 'data', 'container': '/app/data'}]}))

    @pytest.mark.unit
    def test_a_port_outside_the_valid_range_is_refused(self):
        with pytest.raises(schema.AddonError):
            schema.validate(descriptor(container={
                'name': 'demo', 'ports': [{'host': 70000, 'container': 80}]}))

    @pytest.mark.unit
    def test_an_unknown_gpu_mode_is_refused(self):
        with pytest.raises(schema.AddonError):
            schema.validate(descriptor(container={'name': 'demo', 'gpu': 'maybe'}))

    @pytest.mark.unit
    def test_a_malformed_default_tag_is_refused(self):
        with pytest.raises(schema.AddonError):
            schema.validate(descriptor(
                container={'name': 'demo', 'default_tag': 'has space'}))


class TestHealth:
    @pytest.mark.unit
    def test_health_is_required(self):
        # Without it a device cannot tell "enabled but broken" from "disabled".
        with pytest.raises(schema.AddonError) as exc:
            schema.validate(descriptor(health=None))
        assert 'health' in str(exc.value)

    @pytest.mark.unit
    def test_a_container_cannot_be_checked_with_systemd(self):
        with pytest.raises(schema.AddonError):
            schema.validate(descriptor(health={'type': 'systemd', 'unit': 'demo'}))

    @pytest.mark.unit
    def test_an_http_check_needs_a_port_and_an_absolute_path(self):
        with pytest.raises(schema.AddonError):
            schema.validate(descriptor(health={'type': 'http', 'path': '/'}))
        with pytest.raises(schema.AddonError):
            schema.validate(descriptor(health={'type': 'http', 'port': 1, 'path': 'x'}))


class TestSchemaVersion:
    @pytest.mark.unit
    def test_an_unknown_schema_is_refused(self):
        with pytest.raises(schema.AddonError):
            schema.validate(descriptor(schema='flexrun.addon/v2'))

    @pytest.mark.unit
    def test_a_non_object_is_refused(self):
        with pytest.raises(schema.AddonError):
            schema.validate(['not', 'a', 'descriptor'])
