"""Guard against reintroducing the busy-spin that stalled the suite.

Patching time.sleep with anything that returns immediately turns every
`while True: ...; sleep()` daemon in pymongo and redis into a spin - measured
at ~1e6 iterations/second for conftest's double and ~800x for a plain
MagicMock. It also lets those threads' calls land in a mock's call_count, which
is how one assertion here once reported 1404 calls against an expected 1.

Both symptoms are invisible in a single test and only show up as the whole
suite intermittently stalling, so this scans for the pattern rather than
waiting to be bitten again.
"""
import os
import re

import pytest

TESTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# patch('<anything>time.sleep'...) - the module prefix does not make it local:
# routes.system_routes.time IS the global time module.
PATCH_SLEEP = re.compile(r"""patch\(\s*["'][\w.]*time\.sleep["']""")

# 'thread-aware-sleep' marks a hand-written double that has been checked: it
# either sleeps for real on background threads or is a genuine sleep already.
SAFE = ('thread_aware_sleep_mock', 'main_thread_sleep', 'thread-aware-sleep')


def _test_files():
    for root, _dirs, names in os.walk(TESTS_DIR):
        if '__pycache__' in root:
            continue
        for name in names:
            # testsupport.py documents the pattern it exists to replace.
            if name in ('testsupport.py', os.path.basename(__file__)):
                continue
            if name.endswith('.py'):
                yield os.path.join(root, name)


def _offences(text):
    """Lines patching time.sleep with no thread-aware double in the statement."""
    lines = text.splitlines()
    found = []
    for index, line in enumerate(lines):
        if not PATCH_SLEEP.search(line):
            continue
        # A patch call can wrap; look at the continuation too.
        window = ' '.join(lines[index:index + 3])
        if not any(marker in window for marker in SAFE):
            found.append((index + 1, line.strip()))
    return found


@pytest.mark.parametrize('path', sorted(_test_files()),
                         ids=lambda p: os.path.relpath(p, TESTS_DIR))
def test_sleep_is_never_patched_with_a_bare_mock(path):
    with open(path) as handle:
        offences = _offences(handle.read())
    assert not offences, (
        'patch time.sleep with thread_aware_sleep_mock() (new= on a context '
        'manager, new_callable= on a decorator) or main_thread_sleep, so '
        'background threads still sleep:\n'
        + '\n'.join('  line {}: {}'.format(n, l) for n, l in offences))


class TestTheGuardItself:
    """A scanner that cannot fail is worse than no scanner."""

    def test_it_catches_a_bare_patch(self):
        assert _offences("with patch('time.sleep'):\n    pass\n")

    def test_it_catches_a_module_prefixed_patch(self):
        assert _offences("with patch('routes.system_routes.time.sleep'):\n    pass\n")

    def test_it_accepts_the_thread_aware_mock(self):
        assert not _offences(
            "with patch('time.sleep', new=thread_aware_sleep_mock()):\n    pass\n")

    def test_it_accepts_new_callable(self):
        assert not _offences(
            "@patch('time.sleep', new_callable=thread_aware_sleep_mock)\n")

    def test_it_accepts_a_marked_hand_written_double(self):
        assert not _offences(
            "with patch('routes.mqtt_routes.time.sleep',  # thread-aware-sleep\n"
            "           side_effect=sleep):\n")

    def test_it_accepts_main_thread_sleep(self):
        assert not _offences(
            "with patch('time.sleep', side_effect=main_thread_sleep(calls, 2)):\n")

    def test_it_tolerates_a_wrapped_call(self):
        assert not _offences(
            "with patch('ftp_worker.time.sleep',\n"
            "           side_effect=main_thread_sleep(calls)):\n")

    def test_it_ignores_unrelated_patches(self):
        assert not _offences("with patch('os.system'):\n    pass\n")
