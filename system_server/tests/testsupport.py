"""Helpers shared by test modules.

Separate from conftest.py because conftest is not importable from the unit/ and
integration/ subdirectories - pytest loads it, but `from conftest import ...`
only works from the directory it lives in.
"""
import threading
import time as _time

from unittest.mock import MagicMock


_REAL_SLEEP = _time.sleep


class _ThreadAwareSleep(MagicMock):
    """time.sleep double that is invisible to background threads.

    Two problems with a bare patch('time.sleep'), both of which this fixes:

    Daemon threads stop sleeping. Every `while True: ...; sleep()` loop in
    pymongo and redis becomes a busy-spin - measured at ~800x normal speed for
    a plain MagicMock, and ~1e6x for the equivalent bug in conftest's
    main-thread double. That is what made the suite intermittently stall and
    reach tens of GB of RSS.

    Their calls get recorded. A test asserting `sleep.assert_called_once()` is
    then counting the driver's polling as well as its own loop, which is how
    one assertion here once reported 1404 calls against an expected 1.

    Overriding __call__ rather than using side_effect is deliberate: side_effect
    runs after the call is recorded, so it cannot prevent the recording.
    """

    def __call__(self, seconds=0, *args, **kwargs):
        if threading.current_thread() is not threading.main_thread():
            return _REAL_SLEEP(seconds)
        return super().__call__(seconds, *args, **kwargs)


def thread_aware_sleep_mock():
    """A time.sleep double for patch('time.sleep', ...).

    Records main-thread calls like a plain MagicMock, so existing assertions
    keep working. Use new_callable= on a decorator (the mock is still injected
    as an argument) and new= on a context manager.
    """
    return _ThreadAwareSleep(name='time.sleep')
