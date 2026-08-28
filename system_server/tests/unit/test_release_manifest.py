"""The release manifest: what it accepts, and what it must refuse to produce.

The manifest is the artifact a whole fleet's software is decided by, so the
input validation here is load-bearing. Most of these tests assert a *refusal* -
a manifest that is wrong but well-formed is far more dangerous than one that
fails to build.
"""
import datetime
import hashlib
import json
import pytest

from release import manifest as m

NOW = datetime.datetime(2026, 8, 26, 23, 0, 0)
COMMIT = '6647d89c8f9db71c458bc660f78106381b3c7f09'


def fake_resolver(repo, tag):
    """Deterministic stand-in for a registry lookup."""
    return 'sha256:' + hashlib.sha256('{}:{}'.format(repo, tag).encode()).hexdigest()


def all_tags(version='1.9.3', vernemq='prod'):
    tags = {c: version for c in m.FOUNDATIONAL}
    tags['vernemq'] = vernemq
    return tags


def build(**overrides):
    kwargs = dict(release='1.9.3', counter=47, tags=all_tags(),
                  flexrun_commit=COMMIT, resolver=fake_resolver, now=NOW)
    kwargs.update(overrides)
    return m.build_manifest(**kwargs)


class TestBuild:

    def test_covers_every_component_that_exists_for_each_arch(self):
        built = build()
        assert sorted(built['images']) == ['arm', 'x86']
        for arch in ('x86', 'arm'):
            assert set(built['images'][arch]) == set(m.foundational_for_arch(arch))

    def test_visiontools_is_x86_only_for_now(self):
        built = build()
        assert 'visiontools' in built['images']['x86']
        assert 'visiontools' not in built['images']['arm']

    def test_repository_name_includes_the_arch(self):
        built = build()
        assert built['images']['arm']['backend']['repository'] == 'fvonprem/arm-backend'
        assert built['images']['x86']['backend']['repository'] == 'fvonprem/x86-backend'

    def test_same_component_differs_by_arch(self):
        """A single digest for both arches would pin the wrong image on one."""
        built = build()
        assert (built['images']['x86']['backend']['digest']
                != built['images']['arm']['backend']['digest'])

    def test_notafter_follows_valid_days(self):
        built = build(valid_days=30)
        assert built['created'] == '2026-08-26T23:00:00Z'
        assert built['notAfter'] == '2026-09-25T23:00:00Z'

    def test_flexrun_commit_is_pinned(self):
        """Without this the scripts and the container versions can disagree."""
        assert build()['flexrun']['commit'] == COMMIT

    def test_vernemq_keeps_its_environment_tag(self):
        """It is tagged local/prod, not by version - but still pinned."""
        built = build(tags=all_tags(vernemq='local'))
        entry = built['images']['x86']['vernemq']
        assert entry['tag'] == 'local'
        assert entry['digest'].startswith('sha256:')

    def test_timestamps_have_no_sub_second_noise(self):
        built = build(now=datetime.datetime(2026, 8, 26, 23, 0, 0, 123456))
        assert built['created'] == '2026-08-26T23:00:00Z'


class TestBuildRefusals:

    @pytest.mark.parametrize('bad', ['1.9', 'v1.9.3', '1.9.3-rc1', '', 'latest', None])
    def test_release_must_be_a_dotted_version(self, bad):
        with pytest.raises(m.ManifestError, match='release must look like'):
            build(release=bad)

    @pytest.mark.parametrize('bad', [0, -1, '47', 1.0, None, True])
    def test_counter_must_be_a_positive_integer(self, bad):
        """True is an int in Python; a boolean counter would break ordering."""
        with pytest.raises(m.ManifestError, match='counter must be'):
            build(counter=bad)

    @pytest.mark.parametrize('bad', ['6647d89', '', None, 'z' * 40])
    def test_commit_must_be_a_full_sha(self, bad):
        """A short sha is ambiguous, and ambiguity in a pin is not a pin."""
        with pytest.raises(m.ManifestError, match='full 40-character sha'):
            build(flexrun_commit=bad)

    def test_missing_component_is_named(self):
        tags = all_tags()
        del tags['vision']
        del tags['vernemq']
        with pytest.raises(m.ManifestError, match='no tag given for: vernemq, vision'):
            build(tags=tags)

    def test_unknown_component_is_rejected(self):
        """Silently ignoring it would leave the extra image unpinned."""
        tags = all_tags()
        tags['ocr'] = '1.0.0'
        with pytest.raises(m.ManifestError, match='unknown foundational component'):
            build(tags=tags)

    def test_empty_tag_is_rejected(self):
        """An empty version string is exactly how the old pipeline silently
        upgraded nothing - it must never reach a manifest."""
        tags = all_tags()
        tags['frontend'] = ''
        with pytest.raises(m.ManifestError, match='empty tag for frontend'):
            build(tags=tags)

    @pytest.mark.parametrize('bad', [
        None, '', 'sha256:short', 'deadbeef', 'sha256:' + 'g' * 64,
        'sha512:' + 'a' * 64,
    ])
    def test_bad_resolver_output_is_rejected(self, bad):
        """A resolver that returns junk must not produce an unpinnable manifest."""
        with pytest.raises(m.ManifestError, match='invalid digest'):
            build(resolver=lambda repo, tag: bad)


class TestCanonicalBytes:
    """Signing is over bytes, so serialisation must be deterministic."""

    def test_is_stable_across_key_insertion_order(self):
        built = build()
        shuffled = json.loads(json.dumps(built))
        reordered = {k: shuffled[k] for k in reversed(list(shuffled))}
        assert m.canonical_bytes(built) == m.canonical_bytes(reordered)

    def test_is_byte_identical_for_repeated_builds(self):
        assert m.canonical_bytes(build()) == m.canonical_bytes(build())

    def test_ends_with_exactly_one_newline(self):
        raw = m.canonical_bytes(build())
        assert raw.endswith(b'\n')
        assert not raw.endswith(b'\n\n')

    @pytest.mark.parametrize('mutate', [
        lambda d: d.update(release='1.9.4'),
        lambda d: d.update(counter=48),
        lambda d: d.update(notAfter='2099-01-01T00:00:00Z'),
        lambda d: d['flexrun'].update(commit='a' * 40),
        lambda d: d['images']['x86']['backend'].update(digest='sha256:' + 'b' * 64),
        lambda d: d['images']['arm']['vision'].update(tag='9.9.9'),
        lambda d: d.update(notes='tampered'),
    ])
    def test_any_change_changes_the_signed_bytes(self, mutate):
        """If a field could change without changing the bytes, the signature
        would not actually cover it."""
        original = build()
        tampered = json.loads(json.dumps(original))
        mutate(tampered)
        assert m.canonical_bytes(original) != m.canonical_bytes(tampered)

    def test_round_trips(self):
        built = build()
        assert m.loads(m.canonical_bytes(built)) == built


class TestValidate:

    def test_rejects_non_json(self):
        with pytest.raises(m.ManifestError, match='not valid JSON'):
            m.loads('this is not json')

    def test_rejects_a_json_array(self):
        with pytest.raises(m.ManifestError, match='must be a JSON object'):
            m.loads('[]')

    def test_rejects_an_unknown_schema(self):
        built = build()
        built['schema'] = 'flexrun.release/v99'
        with pytest.raises(m.ManifestError, match='unsupported schema'):
            m.validate(built)

    @pytest.mark.parametrize('field', ['release', 'counter', 'created', 'notAfter',
                                       'flexrun', 'images'])
    def test_rejects_a_missing_required_field(self, field):
        built = build()
        del built[field]
        with pytest.raises(m.ManifestError, match='missing'):
            m.validate(built)

    def test_rejects_empty_images(self):
        built = build()
        built['images'] = {}
        with pytest.raises(m.ManifestError, match='non-empty'):
            m.validate(built)

    def test_rejects_a_component_with_no_digest(self):
        built = build()
        del built['images']['x86']['backend']['digest']
        with pytest.raises(m.ManifestError, match='missing digest'):
            m.validate(built)

    def test_rejects_a_malformed_digest(self):
        built = build()
        built['images']['x86']['backend']['digest'] = 'latest'
        with pytest.raises(m.ManifestError, match='malformed digest'):
            m.validate(built)


class TestPinnedReference:

    def test_pins_by_digest_not_tag(self):
        built = build()
        ref = m.pinned_reference(built, 'x86', 'backend')
        assert '@sha256:' in ref
        assert ':1.9.3' not in ref, 'a tag in the pull reference defeats the pin'

    def test_reference_is_pullable_shape(self):
        built = build()
        repo, digest = m.pinned_reference(built, 'arm', 'vision').split('@')
        assert repo == 'fvonprem/arm-vision'
        assert digest == built['images']['arm']['vision']['digest']

    def test_unknown_arch_is_reported_with_what_is_available(self):
        built = build()
        with pytest.raises(m.ManifestError, match='has no images for arch'):
            m.components_for(built, 'riscv')


class TestFeatureServices:
    """Optional services enabled per device. They must still be pinned."""

    def test_features_are_pinned_alongside_foundational(self):
        built = build(features={'eventor': '0.4.1', 'ocr': '2.1.0'})
        assert set(m.features_for(built, 'x86')) == {'eventor', 'ocr'}
        assert set(m.foundational_for(built, 'x86')) == set(m.FOUNDATIONAL)

    def test_a_feature_is_pinned_by_digest_like_anything_else(self):
        built = build(features={'eventor': '0.4.1'})
        entry = built['images']['arm']['eventor']
        assert entry['digest'].startswith('sha256:')
        assert entry['repository'] == 'fvonprem/arm-eventor'
        assert entry['tier'] == m.TIER_FEATURE

    def test_no_features_is_valid(self):
        built = build()
        assert m.features_for(built, 'x86') == {}

    def test_a_component_cannot_be_both_tiers(self):
        with pytest.raises(m.ManifestError, match='both foundational and feature'):
            build(features={'backend': '1.9.3'})

    def test_an_empty_feature_tag_is_rejected(self):
        with pytest.raises(m.ManifestError, match='empty tag for eventor'):
            build(features={'eventor': ''})

    def test_features_change_the_signed_bytes(self):
        assert (m.canonical_bytes(build())
                != m.canonical_bytes(build(features={'eventor': '0.4.1'})))


class TestApplicable:
    """What a specific device should run, given the features it has enabled."""

    def test_foundational_only_when_no_features_enabled(self):
        built = build(features={'eventor': '0.4.1'})
        assert set(m.applicable(built, 'x86', [])) == set(m.FOUNDATIONAL)

    def test_enabled_feature_is_included(self):
        built = build(features={'eventor': '0.4.1', 'ocr': '2.1.0'})
        applied = m.applicable(built, 'x86', ['eventor'])
        assert 'eventor' in applied
        assert 'ocr' not in applied, 'a feature the device has not enabled'

    def test_an_enabled_feature_the_release_does_not_pin_is_an_error(self):
        """Skipping it silently leaves a running container un-upgraded, which
        is how a device ends up on a combination nobody chose."""
        built = build(features={'eventor': '0.4.1'})
        with pytest.raises(m.ManifestError, match='does not pin enabled feature'):
            m.applicable(built, 'x86', ['eventor', 'anomaly'])

    def test_the_error_names_the_missing_feature(self):
        built = build()
        with pytest.raises(m.ManifestError) as exc:
            m.applicable(built, 'x86', ['anomaly'])
        assert 'anomaly' in str(exc.value)


class TestProvenance:
    """A signature proves integrity. It is not evidence of testing."""

    def test_vendored_forks_are_marked_as_such(self):
        built = build()
        for name in ('prediction', 'vernemq', 'nodecreator'):
            assert built['images']['x86'][name]['provenance'] == 'vendored'

    def test_our_own_services_with_a_suite_are_marked_built(self):
        """Only where CI actually runs the suite green: onprembackend and
        visionapi today."""
        built = build()
        for name in ('backend', 'vision'):
            assert built['images']['x86'][name]['provenance'] == 'built'

    def test_our_own_services_without_a_suite_are_marked_untested(self):
        """visiontools is ours and has no tests. Calling it 'built' would make
        the manifest claim a test gate that does not exist."""
        built = build()
        for name in m.UNTESTED:
            assert built['images']['x86'][name]['provenance'] == 'untested'

    def test_untested_and_vendored_do_not_overlap(self):
        assert not (m.VENDORED & m.UNTESTED)

    def test_ungated_lists_everything_without_a_test_gate(self):
        """So a release can say out loud what was not test-gated."""
        assert m.ungated(build()) == sorted(m.NOT_TEST_GATED)

    def test_a_component_with_a_suite_is_not_ungated(self):
        assert 'backend' not in m.ungated(build())

    def test_a_feature_service_is_built_not_vendored(self):
        built = build(features={'eventor': '0.4.1'})
        assert built['images']['x86']['eventor']['provenance'] == 'built'

    def test_provenance_is_covered_by_the_signature(self):
        """Otherwise a vendored image could be relabelled as tested."""
        original = build()
        tampered = json.loads(json.dumps(original))
        tampered['images']['x86']['vernemq']['provenance'] = 'built'
        assert m.canonical_bytes(original) != m.canonical_bytes(tampered)


class TestPerArchTags:
    """x86 and arm run different version streams - backend is 1.97 on x86 and
    1.93 on arm - so one tag for both arches would pin images that do not
    exist. These are the tests that stop a release claiming otherwise."""

    def _split(self, x86_backend='1.97', arm_backend='1.93', drop=None):
        tags = {}
        for arch, backend in (('x86', x86_backend), ('arm', arm_backend)):
            per = {c: '1.0' for c in m.foundational_for_arch(arch)}
            per['backend'] = backend
            if drop and arch == drop[0]:
                del per[drop[1]]
            tags[arch] = per
        return tags

    def test_a_flat_map_still_applies_to_every_arch(self):
        resolved = m.per_arch_tags({'backend': '1.97'})
        assert resolved == {'x86': {'backend': '1.97'},
                            'arm': {'backend': '1.97'}}

    def test_a_per_arch_map_is_kept_apart(self):
        resolved = m.per_arch_tags({'x86': {'backend': '1.97'},
                                    'arm': {'backend': '1.93'}})
        assert resolved['x86']['backend'] == '1.97'
        assert resolved['arm']['backend'] == '1.93'

    def test_a_missing_arch_is_refused(self):
        with pytest.raises(m.ManifestError, match='arm'):
            m.per_arch_tags({'x86': {'backend': '1.97'}})

    def test_each_arch_gets_its_own_tag_in_the_manifest(self):
        built = build(tags=self._split())
        assert built['images']['x86']['backend']['tag'] == '1.97'
        assert built['images']['arm']['backend']['tag'] == '1.93'

    def test_each_arch_gets_its_own_digest(self):
        """Different tag on a different repository is a different image."""
        built = build(tags=self._split())
        assert built['images']['x86']['backend']['digest'] \
            != built['images']['arm']['backend']['digest']

    def test_a_component_missing_on_one_arch_is_refused(self):
        """The failure mode behind arm-visiontools never upgrading: a component
        absent from the version source. Now it cannot be released."""
        with pytest.raises(m.ManifestError, match='vision.*arm|arm.*vision'):
            build(tags=self._split(drop=('arm', 'vision')))

    def test_pinning_a_component_an_arch_does_not_have_is_refused(self):
        """Explicitly naming visiontools under arm pins an image nobody can
        pull, so it is an error rather than something quietly dropped."""
        tags = self._split()
        tags['arm']['visiontools'] = '0.43'
        with pytest.raises(m.ManifestError, match='not built for arm'):
            build(tags=tags)

    def test_an_arch_missing_a_component_names_the_arch(self):
        with pytest.raises(m.ManifestError) as exc:
            build(tags=self._split(drop=('x86', 'vernemq')))
        assert 'x86' in str(exc.value) and 'vernemq' in str(exc.value)

    def test_vernemq_is_pinned_where_it_exists(self):
        """It carries a channel name rather than a version, but is still pinned
        by digest. x86 only: fvonprem/arm-vernemq is not in the registry."""
        built = build(tags=self._split())
        assert built['images']['x86']['vernemq']['digest'].startswith('sha256:')
        assert 'vernemq' not in built['images']['arm']

    def test_features_may_differ_per_arch(self):
        built = build(tags=self._split(),
                      features={'x86': {'eventor': '0.4.1'}, 'arm': {'eventor': '0.3.0'}})
        assert built['images']['x86']['eventor']['tag'] == '0.4.1'
        assert built['images']['arm']['eventor']['tag'] == '0.3.0'

    def test_a_flat_feature_map_still_works(self):
        built = build(tags=self._split(), features={'eventor': '0.4.1'})
        assert built['images']['arm']['eventor']['tag'] == '0.4.1'
