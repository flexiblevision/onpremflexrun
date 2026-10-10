"""Device-side release state.

The load-bearing behaviour is that a rollback moves `installed` without moving
`high_water`. Get that wrong in either direction and you either strand the
device on an old release or let the channel push it straight back down.
"""
import datetime
import pytest

from release import state as st

NOW = datetime.datetime(2026, 8, 27, 10, 0, 0)


class FakeCollection:
    """Stands in for the mongo utils collection."""

    def __init__(self, doc=None):
        self.doc = dict(doc) if doc else None
        self.writes = 0

    def find_one(self, query, projection=None):
        if not self.doc:
            return None
        return {k: v for k, v in self.doc.items() if k != '_id'}

    def update_one(self, query, update, upsert=False):
        self.doc = dict(self.doc or {})
        self.doc.update(update['$set'])
        self.writes += 1


def manifest(counter, release=None):
    return {'counter': counter, 'release': release or '1.9.%d' % counter}


class TestBlankDevice:

    def test_a_device_that_never_applied_a_release(self):
        state = st.read(FakeCollection())
        assert state == {'installed': None, 'high_water': 0, 'history': []}

    def test_high_water_zero_accepts_the_first_release(self):
        """verify() requires counter > high_water, so 0 lets counter 1 through."""
        assert st.read(FakeCollection())['high_water'] == 0

    def test_nothing_to_roll_back_to(self):
        assert st.rollback_targets(FakeCollection()) == []
        assert st.known_counters(FakeCollection()) == set()


class TestRecordApplied:

    def test_an_upgrade_moves_both_installed_and_high_water(self):
        c = FakeCollection()
        st.record_applied(c, manifest(47), now=NOW)
        state = st.read(c)
        assert state['installed']['counter'] == 47
        assert state['high_water'] == 47

    def test_history_accumulates(self):
        c = FakeCollection()
        for n in (46, 47, 48):
            st.record_applied(c, manifest(n), now=NOW)
        assert [h['counter'] for h in st.read(c)['history']] == [46, 47, 48]

    def test_the_release_string_is_kept_for_display(self):
        c = FakeCollection()
        st.record_applied(c, manifest(47, '1.9.47'), now=NOW)
        assert st.read(c)['installed']['release'] == '1.9.47'

    def test_applied_at_is_stamped(self):
        c = FakeCollection()
        st.record_applied(c, manifest(47), now=NOW)
        assert st.read(c)['installed']['applied_at'] == '2026-08-27T10:00:00Z'

    def test_reapplying_a_release_does_not_duplicate_history(self):
        c = FakeCollection()
        st.record_applied(c, manifest(47), now=NOW)
        st.record_applied(c, manifest(47), now=NOW)
        assert [h['counter'] for h in st.read(c)['history']] == [47]

    def test_history_is_capped(self):
        c = FakeCollection()
        for n in range(1, st.MAX_HISTORY + 6):
            st.record_applied(c, manifest(n), now=NOW)
        history = st.read(c)['history']
        assert len(history) == st.MAX_HISTORY
        # The oldest are dropped, not the newest.
        assert history[-1]['counter'] == st.MAX_HISTORY + 5

    @pytest.mark.parametrize('bad', [None, '47', 1.5, True])
    def test_a_manifest_without_a_usable_counter_is_refused(self, bad):
        with pytest.raises(st.StateError, match='no usable counter'):
            st.record_applied(FakeCollection(), {'counter': bad}, now=NOW)


class TestRollbackLeavesHighWaterAlone:
    """The whole reason installed and high_water are separate values."""

    def _rolled_back(self):
        c = FakeCollection()
        st.record_applied(c, manifest(46), now=NOW)
        st.record_applied(c, manifest(47), now=NOW)
        st.record_applied(c, manifest(46), now=NOW, rolled_back=True)
        return c

    def test_installed_goes_back(self):
        assert st.read(self._rolled_back())['installed']['counter'] == 46

    def test_high_water_stays_put(self):
        """Otherwise the channel serving 47 would push the device straight
        back to the release it just left."""
        assert st.read(self._rolled_back())['high_water'] == 47

    def test_the_rolled_back_release_stays_in_history(self):
        """It has to remain a rollback target - the operator may want to retry."""
        assert 47 in st.known_counters(self._rolled_back())

    def test_the_rollback_is_recorded_as_such(self):
        c = self._rolled_back()
        assert c.doc['last_change_was_rollback'] is True

    def test_a_later_upgrade_still_raises_high_water(self):
        c = self._rolled_back()
        st.record_applied(c, manifest(48), now=NOW)
        assert st.read(c)['high_water'] == 48
        assert st.read(c)['installed']['counter'] == 48


class TestRollbackTargets:

    def test_excludes_what_is_running(self):
        c = FakeCollection()
        for n in (46, 47, 48):
            st.record_applied(c, manifest(n), now=NOW)
        assert [t['counter'] for t in st.rollback_targets(c)] == [47, 46]

    def test_newest_first(self):
        """Ordered by counter, not by when they were applied - an operator
        thinks in release order."""
        c = FakeCollection()
        for n in (10, 30, 20, 15):
            st.record_applied(c, manifest(n), now=NOW)
        # installed is the last applied (15), so it is excluded
        assert [t['counter'] for t in st.rollback_targets(c)] == [30, 20, 10]

    def test_known_counters_includes_the_installed_one(self):
        """Reapplying the current release is a legitimate operator action."""
        c = FakeCollection()
        st.record_applied(c, manifest(47), now=NOW)
        assert st.known_counters(c) == {47}


class TestCorruptedState:

    def test_a_high_water_below_installed_is_repaired_on_read(self):
        """Trusting it would re-open the downgrade the counter prevents."""
        c = FakeCollection({'type': st.STATE_TYPE,
                            'installed': {'counter': 47},
                            'high_water': 3,
                            'history': []})
        assert st.read(c)['high_water'] == 47

    @pytest.mark.parametrize('bad', [None, 'x', 1.5])
    def test_a_non_integer_high_water_is_repaired(self, bad):
        c = FakeCollection({'type': st.STATE_TYPE,
                            'installed': {'counter': 12},
                            'high_water': bad, 'history': []})
        assert st.read(c)['high_water'] == 12

    def test_a_non_list_history_is_repaired(self):
        c = FakeCollection({'type': st.STATE_TYPE, 'installed': None,
                            'high_water': 0, 'history': 'nonsense'})
        assert st.read(c)['history'] == []

    def test_a_database_failure_is_raised_not_swallowed(self):
        class Broken:
            def find_one(self, *a, **k):
                raise RuntimeError('no mongo')
        with pytest.raises(st.StateError, match='could not read'):
            st.read(Broken())


class TestSummary:
    """What the settings screen renders."""

    def _at(self, installed, high_water_extra=None):
        c = FakeCollection()
        st.record_applied(c, manifest(installed), now=NOW)
        if high_water_extra:
            st.record_applied(c, manifest(high_water_extra), now=NOW)
            st.record_applied(c, manifest(installed), now=NOW, rolled_back=True)
        return c

    def test_reports_a_genuine_update(self):
        result = st.summary(self._at(47), available=manifest(48))
        assert result['update_available'] is True
        assert result['rolled_back_from'] is None

    def test_a_release_the_device_rolled_back_from_is_not_an_update(self):
        """Nagging an operator to reapply the release they just abandoned is
        the fastest way to have them ignore the update prompt entirely."""
        result = st.summary(self._at(46, high_water_extra=47),
                            available=manifest(47))
        assert result['update_available'] is False
        assert result['rolled_back_from']['counter'] == 47

    def test_a_newer_release_after_a_rollback_is_still_an_update(self):
        result = st.summary(self._at(46, high_water_extra=47),
                            available=manifest(48))
        assert result['update_available'] is True

    def test_no_channel_information_is_not_an_update(self):
        result = st.summary(self._at(47), available=None)
        assert result['update_available'] is False
        assert result['available'] is None

    def test_history_is_newest_first_for_display(self):
        c = FakeCollection()
        for n in (46, 47, 48):
            st.record_applied(c, manifest(n), now=NOW)
        assert [h['counter'] for h in st.summary(c)['history']] == [48, 47, 46]

    def test_a_blank_device_summarises_without_error(self):
        result = st.summary(FakeCollection())
        assert result['installed'] is None
        assert result['high_water'] == 0
        assert result['rollback_targets'] == []
