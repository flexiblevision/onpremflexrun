"""The rule: a release only pins images CI built from master.

Enforced by reading the OCI revision label rather than asking GitHub which
branch a commit is on - each repo's CI gates its image job on master, so an
image CI published is from master by construction.
"""
import io

import pytest

from release import provenance as p

REV = 'org.opencontainers.image.revision'
SRC = 'org.opencontainers.image.source'
SHA = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4'


def manifest(images):
    return {'images': images}


class TestClassify:

    def test_a_real_commit_is_ok(self):
        assert p.classify({REV: SHA}) == (p.OK, SHA)

    def test_no_labels_at_all_is_missing(self):
        assert p.classify({})[0] == p.MISSING

    def test_none_is_missing(self):
        assert p.classify(None)[0] == p.MISSING

    def test_a_dirty_build_is_not_ok(self):
        """build.sh only appends -dirty when told to build from a working tree.
        A CI checkout is always clean, so this image came from a laptop."""
        status, revision = p.classify({REV: SHA + '-dirty'})
        assert status == p.DIRTY
        assert revision.endswith('-dirty')

    def test_the_unknown_fallback_is_missing_not_ok(self):
        """build.sh writes 'unknown' when git is unavailable. Treating that as a
        commit would record a provenance that is confidently wrong."""
        assert p.classify({REV: 'unknown'})[0] == p.MISSING

    def test_whitespace_only_is_missing(self):
        assert p.classify({REV: '   '})[0] == p.MISSING


class TestInheritedLabelsDoNotCount:
    """Every image inherits org.opencontainers.image.{ref.name,version} from
    its base. Counting those as ours would give a clean bill of health to
    exactly the images that have no provenance at all - which is the live
    state of every fvonprem image today."""

    def test_ubuntu_base_labels_alone_are_missing(self):
        records = p.audit(
            manifest({'x86': {'backend': {'repository': 'fvonprem/x86-backend',
                                          'tag': '1.97'}}}),
            lambda repo, tag: {'org.opencontainers.image.ref.name': 'ubuntu',
                               'org.opencontainers.image.version': '20.04'})
        assert records[0]['status'] == p.MISSING

    def test_the_version_label_is_not_mistaken_for_a_revision(self):
        assert p.classify({'org.opencontainers.image.version': '20.04'})[0] == p.MISSING


class TestAudit:

    def _manifest(self):
        return manifest({
            'x86': {
                'backend': {'repository': 'fvonprem/x86-backend', 'tag': '1.97'},
                'vision': {'repository': 'fvonprem/x86-vision', 'tag': '1.2'},
            },
            'arm': {
                'backend': {'repository': 'fvonprem/arm-backend', 'tag': '1.93'},
            },
        })

    def test_every_image_on_every_arch_is_checked(self):
        records = p.audit(self._manifest(), lambda repo, tag: {REV: SHA})
        assert len(records) == 3
        assert {r['arch'] for r in records} == {'x86', 'arm'}

    def test_the_order_is_stable(self):
        fetch = lambda repo, tag: {REV: SHA}
        first = [(r['arch'], r['component']) for r in p.audit(self._manifest(), fetch)]
        second = [(r['arch'], r['component']) for r in p.audit(self._manifest(), fetch)]
        assert first == second == [('arm', 'backend'), ('x86', 'backend'), ('x86', 'vision')]

    def test_it_records_which_repository_and_tag_it_judged(self):
        records = p.audit(self._manifest(), lambda repo, tag: {REV: SHA})
        backend = [r for r in records if r['arch'] == 'x86' and r['component'] == 'backend'][0]
        assert backend['repository'] == 'fvonprem/x86-backend'
        assert backend['tag'] == '1.97'

    def test_a_mixed_fleet_reports_only_the_bad_ones(self):
        def fetch(repo, tag):
            return {REV: SHA} if 'vision' in repo else {}
        records = p.audit(self._manifest(), fetch)
        assert {r['component'] for r in p.shortfall(records)} == {'backend'}

    def test_the_source_label_is_carried_through(self):
        records = p.audit(
            manifest({'x86': {'vision': {'repository': 'r', 'tag': 't'}}}),
            lambda repo, tag: {REV: SHA, SRC: 'https://github.com/x/y'})
        assert records[0]['source'] == 'https://github.com/x/y'


class TestDescribe:

    def _records(self, statuses):
        return [{'arch': 'x86', 'component': name, 'repository': 'fvonprem/x86-' + name,
                 'tag': '1.0', 'status': status,
                 'revision': SHA if status == p.OK else
                             (SHA + '-dirty' if status == p.DIRTY else None),
                 'source': None}
                for name, status in statuses.items()]

    def test_all_clean_does_not_block(self):
        out = io.StringIO()
        assert p.describe(self._records({'backend': p.OK}), out, strict=True) is False
        assert 'records the commit' in out.getvalue()

    def test_strict_blocks_on_a_missing_label(self):
        out = io.StringIO()
        assert p.describe(self._records({'backend': p.MISSING}), out, strict=True) is True

    def test_strict_blocks_on_a_dirty_build(self):
        out = io.StringIO()
        assert p.describe(self._records({'backend': p.DIRTY}), out, strict=True) is True

    def test_warn_mode_does_not_block(self):
        """Release 1 has to pin what the fleet already runs, and none of those
        images carry a label. Enforcing on day one would block it outright."""
        out = io.StringIO()
        assert p.describe(self._records({'backend': p.MISSING}), out, strict=False) is False
        assert 'Allowed for now' in out.getvalue()

    def test_warn_mode_still_names_what_is_wrong(self):
        out = io.StringIO()
        p.describe(self._records({'backend': p.MISSING}), out, strict=False)
        text = out.getvalue()
        assert 'backend' in text
        assert '1 of 1' in text

    def test_a_dirty_build_says_so_rather_than_just_failing(self):
        out = io.StringIO()
        p.describe(self._records({'backend': p.DIRTY}), out, strict=False)
        assert 'dirty tree' in out.getvalue()

    def test_the_default_is_warn_not_refuse(self):
        """Flipping this to strict is a deliberate act once every component has
        been rebuilt through CI - not something that happens by surprise."""
        assert p.STRICT is False
        out = io.StringIO()
        assert p.describe(self._records({'backend': p.MISSING}), out) is False
