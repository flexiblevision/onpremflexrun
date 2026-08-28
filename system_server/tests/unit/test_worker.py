"""The rq worker daemon entrypoint.

The previous version of this file read worker.py as text and asserted that
certain substrings appeared in it - 'Connection' is in the import line,
'Worker(' is in the body. It never imported the module, so worker.py sat at 0%
coverage while 24 tests reported green, and a NameError at startup would have
passed every one of them.

These run the module: the import path as the daemon sees it, and the __main__
block through runpy with run_name='__main__', which is how
`forever start -c python3 worker.py` invokes it.
"""
import os
import runpy
import sys
from contextlib import contextmanager

import pytest
from unittest.mock import patch, MagicMock


WORKER_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'worker.py'))


class _ConnectionShim:
    """Stand-in for rq.Connection, which rq >= 2.0 removed."""

    def __init__(self, connection=None):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@contextmanager
def _rq_providing_connection():
    """Let the logic tests run against either pinned or newer rq.

    requirements.txt pins rq==1.5.0, which has Connection. A newer rq in the
    working environment does not, and worker.py would fail to import at all.
    The pin itself is guarded by TestRqPinCompatibility; the rest of this file
    is about the worker's behaviour, so the name is supplied when absent.
    """
    import rq
    if hasattr(rq, 'Connection'):
        yield rq
        return

    rq.Connection = _ConnectionShim
    try:
        yield rq
    finally:
        del rq.Connection


@pytest.fixture
def worker():
    with _rq_providing_connection():
        sys.modules.pop('worker', None)
        import worker as module
        yield module
    sys.modules.pop('worker', None)


class TestRqPinCompatibility:
    """worker.py cannot run on rq 2.x, and the pin is the only thing stopping it."""

    @pytest.mark.unit
    def test_the_pinned_rq_still_provides_the_connection_context(self):
        # worker.py does `from rq import Worker, Queue, Connection`. rq 2.0
        # removed Connection outright, so bumping the pin past 1.x turns every
        # worker process into an ImportError at startup - six of them, on every
        # device, with no route that reports the failure.
        #
        # This asserts the pin, not the interpreter's current site-packages: a
        # developer machine that has drifted off requirements.txt is a local
        # environment problem, while a bumped pin ships to the fleet.
        import re

        requirements = os.path.join(
            os.path.dirname(__file__), '..', '..', '..', 'requirements.txt')
        with open(requirements) as f:
            pinned = re.search(r'^rq==(\d+)\.(\d+)', f.read(), re.MULTILINE)

        assert pinned, 'requirements.txt no longer pins rq'
        major = int(pinned.group(1))
        assert major < 2, (
            f'requirements.txt pins rq {pinned.group(0).split("==")[1]}, but rq 2.0 '
            'removed rq.Connection, which worker.py imports. Port worker.py off '
            'the Connection context manager before raising this pin.')


class TestWorkerModule:
    @pytest.mark.unit
    def test_listens_on_the_default_queue(self, worker):
        # Everything enqueued by the routes goes to 'default'; a mismatch here
        # means jobs are accepted and never run.
        assert worker.listen == ['default']

    @pytest.mark.unit
    def test_connects_to_the_local_redis(self, worker):
        assert worker.redis_url == 'redis://localhost:6379'

    @pytest.mark.unit
    def test_a_connection_object_is_built_at_import(self, worker):
        assert worker.conn is not None
        assert hasattr(worker.conn, 'ping')

    @pytest.mark.unit
    def test_the_repo_root_is_on_the_path_so_settings_resolves(self, worker):
        # worker.py appends $HOME/flex-run so `import settings` works when the
        # daemon is started from an arbitrary cwd.
        assert worker.settings_path == os.environ['HOME'] + '/flex-run'
        assert worker.settings_path in sys.path

    @pytest.mark.unit
    def test_importing_does_not_start_working(self):
        # Import must be inert: the module is imported by the test suite and
        # by any tooling that inspects it.
        with _rq_providing_connection() as rq:
            with patch.object(rq, 'Worker') as rq_worker:
                sys.modules.pop('worker', None)
                import worker  # noqa: F401
        rq_worker.assert_not_called()
        sys.modules.pop('worker', None)


class TestWorkerMain:
    """The __main__ block, run the way forever runs it."""

    def _run_main(self):
        rq = MagicMock()
        rq.Connection.return_value.__enter__ = MagicMock()
        rq.Connection.return_value.__exit__ = MagicMock(return_value=False)
        with patch.dict(sys.modules, {'rq': rq}), \
             patch('redis.from_url', return_value=MagicMock()):
            runpy.run_path(WORKER_PATH, run_name='__main__')
        return rq

    @pytest.mark.unit
    def test_starts_a_worker_inside_a_connection_context(self):
        # Without the Connection context rq resolves no default connection and
        # the worker dies on startup.
        rq = self._run_main()
        rq.Connection.assert_called_once_with(rq.Connection.call_args[0][0])
        rq.Connection.return_value.__enter__.assert_called_once()

    @pytest.mark.unit
    def test_the_worker_is_given_the_listen_queues(self):
        rq = self._run_main()
        rq.Queue.assert_called_once_with('default')
        assert rq.Worker.call_args[0][0] == [rq.Queue.return_value]

    @pytest.mark.unit
    def test_work_is_started_with_the_scheduler(self):
        # result_ttl=-1 jobs and the nightly sync both rely on the scheduler.
        rq = self._run_main()
        rq.Worker.return_value.work.assert_called_once_with(with_scheduler=True)

    @pytest.mark.unit
    def test_the_connection_context_wraps_the_redis_handle(self):
        rq = MagicMock()
        conn = MagicMock()
        with patch.dict(sys.modules, {'rq': rq}), \
             patch('redis.from_url', return_value=conn):
            runpy.run_path(WORKER_PATH, run_name='__main__')

        assert rq.Connection.call_args[0][0] is conn

    @pytest.mark.unit
    def test_every_name_the_main_block_uses_resolves(self):
        # The regression this replaces: a name used in the __main__ block but
        # missing from the imports raised NameError on every start. Executing
        # the block catches that; grepping the source does not. run_path uses
        # the module's real globals, so an unimported name raises here.
        rq = self._run_main()
        rq.Worker.return_value.work.assert_called_once()
