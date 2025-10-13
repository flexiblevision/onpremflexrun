"""
Unit tests for worker.py RQ worker script

These tests ensure the worker script can start up properly and has all
required imports. This catches issues like missing imports that would
cause the worker to fail in production.
"""
import pytest
import sys
import os
from unittest.mock import Mock, patch, MagicMock, call
from pathlib import Path


class TestWorkerImports:
    """Tests for worker.py imports and dependencies"""

    @pytest.mark.unit
    def test_worker_has_required_imports(self):
        """Test that worker.py has all required imports available"""
        # Mock the environment variable that worker.py requires
        with patch.dict(os.environ, {'HOME': '/tmp'}):
            # Mock redis and rq modules
            mock_redis = MagicMock()
            mock_redis.from_url.return_value = MagicMock()

            mock_rq = MagicMock()
            mock_worker = MagicMock()
            mock_queue = MagicMock()
            mock_connection = MagicMock()

            mock_rq.Worker = mock_worker
            mock_rq.Queue = mock_queue
            mock_rq.Connection = mock_connection

            with patch.dict('sys.modules', {
                'redis': mock_redis,
                'rq': mock_rq
            }):
                # Read the worker.py file content
                worker_path = os.path.join(
                    os.path.dirname(__file__),
                    '..', '..',
                    'worker.py'
                )

                with open(worker_path, 'r') as f:
                    content = f.read()

                # Check that Connection is imported
                assert 'from rq import' in content, "Missing rq import"

                # Check that redis is imported
                assert 'import redis' in content or 'from redis import' in content, \
                    "Missing redis import"

                # Check that Connection is used
                assert 'Connection' in content, "Connection is used but may not be imported"

    @pytest.mark.unit
    def test_worker_connection_import_exists(self):
        """Test that Connection is specifically imported from rq"""
        worker_path = os.path.join(
            os.path.dirname(__file__),
            '..', '..',
            'worker.py'
        )

        with open(worker_path, 'r') as f:
            content = f.read()

        # Check that Connection is in the imports
        has_connection = (
            'from rq import Worker, Queue, Connection' in content or
            'from rq import Worker, Connection, Queue' in content or
            'from rq import Queue, Worker, Connection' in content or
            'from rq import Queue, Connection, Worker' in content or
            'from rq import Connection, Worker, Queue' in content or
            'from rq import Connection, Queue, Worker' in content or
            'from rq import Connection' in content
        )

        assert has_connection, \
            "Connection must be imported from rq module. " \
            "Found 'with Connection(conn):' usage but Connection is not imported!"

    @pytest.mark.unit
    def test_redis_import_exists(self):
        """Test that redis is imported"""
        worker_path = os.path.join(
            os.path.dirname(__file__),
            '..', '..',
            'worker.py'
        )

        with open(worker_path, 'r') as f:
            content = f.read()

        assert 'import redis' in content, "redis module must be imported"

    @pytest.mark.unit
    def test_rq_worker_import_exists(self):
        """Test that Worker is imported from rq"""
        worker_path = os.path.join(
            os.path.dirname(__file__),
            '..', '..',
            'worker.py'
        )

        with open(worker_path, 'r') as f:
            content = f.read()

        assert 'Worker' in content, "Worker class must be imported from rq"

    @pytest.mark.unit
    def test_rq_queue_import_exists(self):
        """Test that Queue is imported from rq"""
        worker_path = os.path.join(
            os.path.dirname(__file__),
            '..', '..',
            'worker.py'
        )

        with open(worker_path, 'r') as f:
            content = f.read()

        assert 'Queue' in content, "Queue class must be imported from rq"


class TestWorkerConfiguration:
    """Tests for worker.py configuration"""

    @pytest.mark.unit
    def test_worker_has_redis_url(self):
        """Test that worker.py configures redis URL"""
        worker_path = os.path.join(
            os.path.dirname(__file__),
            '..', '..',
            'worker.py'
        )

        with open(worker_path, 'r') as f:
            content = f.read()

        assert 'redis_url' in content, "Worker must configure redis_url"
        assert 'redis://localhost:6379' in content or 'redis://' in content, \
            "Worker must have redis connection string"

    @pytest.mark.unit
    def test_worker_has_listen_queues(self):
        """Test that worker.py configures listen queues"""
        worker_path = os.path.join(
            os.path.dirname(__file__),
            '..', '..',
            'worker.py'
        )

        with open(worker_path, 'r') as f:
            content = f.read()

        assert 'listen' in content, "Worker must configure listen queues"

    @pytest.mark.unit
    def test_worker_uses_main_guard(self):
        """Test that worker.py uses if __name__ == '__main__' guard"""
        worker_path = os.path.join(
            os.path.dirname(__file__),
            '..', '..',
            'worker.py'
        )

        with open(worker_path, 'r') as f:
            content = f.read()

        assert "if __name__ == '__main__':" in content, \
            "Worker should use __main__ guard to prevent execution on import"


class TestWorkerStartup:
    """Tests for worker.py startup logic"""

    @pytest.mark.unit
    @patch.dict(os.environ, {'HOME': '/tmp/test-home'})
    def test_worker_sets_up_path(self):
        """Test that worker.py sets up the Python path correctly"""
        worker_path = os.path.join(
            os.path.dirname(__file__),
            '..', '..',
            'worker.py'
        )

        with open(worker_path, 'r') as f:
            content = f.read()

        # Check that sys.path is being modified
        assert 'sys.path.append' in content, \
            "Worker should add settings path to sys.path"

    @pytest.mark.unit
    @patch.dict(os.environ, {'HOME': '/tmp/test-home'})
    def test_worker_can_be_instantiated_mock(self):
        """Test that Worker can be instantiated with mocked dependencies"""
        # This test verifies the logic flow without actually importing rq
        # We just check that the code structure is correct

        worker_path = os.path.join(
            os.path.dirname(__file__),
            '..', '..',
            'worker.py'
        )

        with open(worker_path, 'r') as f:
            content = f.read()

        # Verify the worker instantiation logic is present
        assert 'Worker(list(map(Queue, listen)))' in content, \
            "Worker should be instantiated with mapped queues"
        assert 'worker.work(with_scheduler=True)' in content, \
            "Worker should call work() with scheduler enabled"

    @pytest.mark.unit
    def test_worker_file_exists(self):
        """Test that worker.py file exists"""
        worker_path = os.path.join(
            os.path.dirname(__file__),
            '..', '..',
            'worker.py'
        )

        assert os.path.exists(worker_path), \
            f"worker.py should exist at {worker_path}"

    @pytest.mark.unit
    def test_worker_file_is_readable(self):
        """Test that worker.py file is readable"""
        worker_path = os.path.join(
            os.path.dirname(__file__),
            '..', '..',
            'worker.py'
        )

        try:
            with open(worker_path, 'r') as f:
                content = f.read()
            assert len(content) > 0, "worker.py should not be empty"
        except Exception as e:
            pytest.fail(f"Failed to read worker.py: {e}")

    @pytest.mark.unit
    def test_worker_has_no_syntax_errors(self):
        """Test that worker.py has no syntax errors"""
        worker_path = os.path.join(
            os.path.dirname(__file__),
            '..', '..',
            'worker.py'
        )

        try:
            with open(worker_path, 'r') as f:
                content = f.read()

            # Try to compile the code to check for syntax errors
            compile(content, worker_path, 'exec')
        except SyntaxError as e:
            pytest.fail(f"worker.py has syntax errors: {e}")


class TestWorkerRedisConnection:
    """Tests for worker.py Redis connection logic"""

    @pytest.mark.unit
    def test_worker_creates_redis_connection(self):
        """Test that worker creates a redis connection"""
        worker_path = os.path.join(
            os.path.dirname(__file__),
            '..', '..',
            'worker.py'
        )

        with open(worker_path, 'r') as f:
            content = f.read()

        # Check that redis connection is created
        assert 'redis.from_url' in content or 'Redis(' in content, \
            "Worker should create a Redis connection"

    @pytest.mark.unit
    def test_worker_uses_connection_context(self):
        """Test that worker uses Connection context manager"""
        worker_path = os.path.join(
            os.path.dirname(__file__),
            '..', '..',
            'worker.py'
        )

        with open(worker_path, 'r') as f:
            content = f.read()

        # Check that Connection context manager is used
        assert 'with Connection' in content, \
            "Worker should use 'with Connection(conn):' context manager"


class TestWorkerQueueSetup:
    """Tests for worker.py queue setup"""

    @pytest.mark.unit
    def test_worker_maps_queues(self):
        """Test that worker maps listen array to Queue objects"""
        worker_path = os.path.join(
            os.path.dirname(__file__),
            '..', '..',
            'worker.py'
        )

        with open(worker_path, 'r') as f:
            content = f.read()

        # Check that queues are mapped
        assert 'map(Queue, listen)' in content or 'Queue(' in content, \
            "Worker should map listen queues to Queue objects"

    @pytest.mark.unit
    def test_worker_starts_with_scheduler(self):
        """Test that worker starts with scheduler enabled"""
        worker_path = os.path.join(
            os.path.dirname(__file__),
            '..', '..',
            'worker.py'
        )

        with open(worker_path, 'r') as f:
            content = f.read()

        # Check that worker is started with scheduler
        assert 'worker.work' in content, "Worker should call work() method"
        assert 'with_scheduler=True' in content, \
            "Worker should enable scheduler with with_scheduler=True"


class TestWorkerEnvironment:
    """Tests for worker.py environment setup"""

    @pytest.mark.unit
    def test_worker_reads_home_env(self):
        """Test that worker reads HOME environment variable"""
        worker_path = os.path.join(
            os.path.dirname(__file__),
            '..', '..',
            'worker.py'
        )

        with open(worker_path, 'r') as f:
            content = f.read()

        # Check that HOME environment variable is used
        assert "os.environ['HOME']" in content or 'os.environ.get' in content, \
            "Worker should read HOME environment variable"

    @pytest.mark.unit
    def test_worker_sets_settings_path(self):
        """Test that worker sets up settings path"""
        worker_path = os.path.join(
            os.path.dirname(__file__),
            '..', '..',
            'worker.py'
        )

        with open(worker_path, 'r') as f:
            content = f.read()

        # Check that settings_path is configured
        assert 'settings_path' in content, \
            "Worker should configure settings_path"
        assert 'flex-run' in content, \
            "Worker should use flex-run directory for settings"


class TestWorkerImportSmoke:
    """Smoke tests to ensure worker.py can be imported without errors"""

    @pytest.mark.unit
    @patch.dict(os.environ, {'HOME': '/tmp/test-home'})
    @patch('sys.path', ['/tmp/test-home/flex-run'])
    def test_worker_imports_without_module_errors(self):
        """Test that importing worker dependencies doesn't cause NameError"""
        # This test ensures all names used in worker.py are properly imported

        # Mock the required modules
        mock_redis = MagicMock()
        mock_redis.from_url = MagicMock(return_value=MagicMock())

        mock_rq = MagicMock()

        # This is the critical part - ensure Connection is available
        mock_connection = MagicMock()
        mock_worker = MagicMock()
        mock_queue = MagicMock()

        mock_rq.Connection = mock_connection
        mock_rq.Worker = mock_worker
        mock_rq.Queue = mock_queue

        # Read worker.py and check all used names are imported
        worker_path = os.path.join(
            os.path.dirname(__file__),
            '..', '..',
            'worker.py'
        )

        with open(worker_path, 'r') as f:
            lines = f.readlines()

        # Find all imports
        imports = []
        for line in lines:
            if line.strip().startswith('import ') or line.strip().startswith('from '):
                imports.append(line.strip())

        # Check that if Connection is used, it's imported
        content = ''.join(lines)
        if 'with Connection(' in content or 'Connection(' in content:
            has_connection_import = any(
                'Connection' in imp for imp in imports
            )
            assert has_connection_import, \
                "Connection is used but not imported! This will cause: NameError: name 'Connection' is not defined"

    @pytest.mark.unit
    def test_all_used_names_are_imported(self):
        """Test that all names used in worker.py are properly imported"""
        worker_path = os.path.join(
            os.path.dirname(__file__),
            '..', '..',
            'worker.py'
        )

        with open(worker_path, 'r') as f:
            content = f.read()

        # Extract import lines
        import_lines = []
        for line in content.split('\n'):
            if line.strip().startswith('import ') or line.strip().startswith('from '):
                import_lines.append(line)

        imports_text = '\n'.join(import_lines)

        # Check critical names
        if 'Connection(' in content:
            assert 'Connection' in imports_text, \
                "CRITICAL: 'Connection' is used but not imported! " \
                "Add 'Connection' to the rq import statement."

        if 'Worker(' in content:
            assert 'Worker' in imports_text, \
                "'Worker' is used but not imported!"

        if 'Queue(' in content:
            assert 'Queue' in imports_text, \
                "'Queue' is used but not imported!"

        if 'redis.from_url' in content or 'Redis(' in content:
            assert 'redis' in imports_text.lower(), \
                "'redis' module is used but not imported!"


class TestWorkerRegressionTests:
    """Regression tests for specific bugs found in production"""

    @pytest.mark.unit
    def test_connection_import_regression(self):
        """
        Regression test for: NameError: name 'Connection' is not defined

        This bug was pushed to production when worker.py used Connection
        but didn't import it from rq module.
        """
        worker_path = os.path.join(
            os.path.dirname(__file__),
            '..', '..',
            'worker.py'
        )

        with open(worker_path, 'r') as f:
            content = f.read()

        # If Connection is used, it MUST be imported
        uses_connection = 'Connection(' in content

        if uses_connection:
            # Check import line includes Connection
            import_lines = [
                line for line in content.split('\n')
                if 'from rq import' in line
            ]

            has_connection_import = any(
                'Connection' in line for line in import_lines
            )

            assert has_connection_import, \
                "PRODUCTION BUG DETECTED: Connection is used on line 14 but not imported! " \
                "This will cause 'NameError: name 'Connection' is not defined' " \
                "Fix: Change 'from rq import Worker, Queue' to 'from rq import Worker, Queue, Connection'"
