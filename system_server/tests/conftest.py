"""
Pytest configuration and shared fixtures for the test suite.
"""
import os
import sys
import pytest
from unittest.mock import Mock, MagicMock, patch
from flask import Flask
from flask_restx import Api

# Add parent directory to path (system_server)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
# Add grandparent directory to path (onpremflexrun - for settings.py)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

# Mock settings configuration at module level BEFORE any imports
# Create a dict-like mock that handles both attribute and key access
class MockConfig(dict):
    """Mock config that works as both dict and object"""
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(f"Config has no attribute '{key}'")

    def __setattr__(self, key, value):
        self[key] = value

_mock_config = MockConfig({
    'cloud_domain': 'https://test.flexiblevision.com',
    'auth0_domain': 'auth.test.com',
    'auth_alg': 'RS256',
    'auth0_CID': 'test_client_id',
    'environ': 'test',
    'jwt_secret_key': 'test_secret',
    'static_ip': '192.168.10.35',
    'ssid': 'test_hotspot',
    'latest_stable_ref': 'test_version',
    'use_aws': False
})

# Create a mock settings module
_mock_settings_module = MagicMock()
_mock_settings_module.config = _mock_config
_mock_settings_module.kinesis = None
_mock_settings_module.FireOperator = None

# Patch settings module before any imports happen
# This prevents Python from loading the real settings.py even if it's found in sys.path
sys.modules['settings'] = _mock_settings_module

# Add an import hook to prevent the real settings from being loaded
import builtins
_original_import = builtins.__import__

def _custom_import(name, *args, **kwargs):
    if name == 'settings':
        # Always return our mock
        return sys.modules['settings']
    return _original_import(name, *args, **kwargs)

builtins.__import__ = _custom_import

# Also mock setup.management to prevent CONFIG messages and file operations
_mock_management = MagicMock()
_mock_management.generate_environment_config = MagicMock(return_value=None)
sys.modules['setup'] = MagicMock()
sys.modules['setup'].management = _mock_management
sys.modules['setup.management'] = _mock_management

# Don't mock pymongo/redis at module level - they need to load properly
# Individual tests will mock database connections as needed

# Mock GPIO module to prevent loading hardware-specific .so files
_mock_gpio_helper = MagicMock()
_mock_gpio_helper.toggle_pin = MagicMock(return_value=True)
_mock_gpio_helper.set_pin_state = MagicMock(return_value=True)
_mock_gpio_helper.read_pin = MagicMock(return_value=0)

_mock_gpio_package = MagicMock()
_mock_gpio_package.gpio_helper = _mock_gpio_helper

sys.modules['gpio'] = _mock_gpio_package
sys.modules['gpio.gpio_helper'] = _mock_gpio_helper

@pytest.fixture(scope='session')
def mock_settings():
    """Mock settings configuration fixture for tests that need to modify settings"""
    yield _mock_config

@pytest.fixture(scope='session')
def app(mock_settings):
    """Create Flask application for testing"""
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.config['DEBUG'] = False

    # Create API instance
    api = Api(app)

    yield app

@pytest.fixture(scope='function')
def client(app):
    """Create Flask test client"""
    return app.test_client()

@pytest.fixture(scope='function')
def mock_mongo_client():
    """Mock MongoDB client"""
    mock_client = MagicMock()
    mock_db = MagicMock()
    mock_collection = MagicMock()

    # Setup mock structure
    mock_client.__getitem__.return_value = mock_db
    mock_db.__getitem__.return_value = mock_collection

    with patch('pymongo.MongoClient', return_value=mock_client):
        yield {
            'client': mock_client,
            'db': mock_db,
            'collection': mock_collection
        }

@pytest.fixture(scope='function')
def mock_redis():
    """Fake Redis connection using fakeredis"""
    import fakeredis
    fake_redis = fakeredis.FakeStrictRedis()

    with patch('redis.Redis', return_value=fake_redis):
        yield fake_redis

@pytest.fixture(scope='function')
def mock_job_queue(mock_redis):
    """Mock RQ job queue with fakeredis backend"""
    mock_queue = MagicMock()
    mock_queue.enqueue.return_value = MagicMock(id='test_job_id')
    mock_queue.count = 0

    # Allow the queue to use fake redis for any redis operations
    mock_queue.connection = mock_redis

    with patch('rq.Queue', return_value=mock_queue):
        yield mock_queue

@pytest.fixture(scope='function')
def auth_headers():
    """Generate mock authentication headers"""
    return {
        'Authorization': 'Bearer test_token_12345',
        'Access-Token': 'test_access_token',
        'Content-Type': 'application/json'
    }

@pytest.fixture(scope='function')
def mock_subprocess():
    """Mock subprocess calls"""
    with patch('subprocess.Popen') as mock_popen, \
         patch('subprocess.call') as mock_call, \
         patch('subprocess.check_output') as mock_check_output, \
         patch('subprocess.getoutput') as mock_getoutput:

        # Setup default return values
        mock_process = MagicMock()
        mock_process.communicate.return_value = (b'mock_output', b'')
        mock_popen.return_value = mock_process

        mock_call.return_value = 0
        mock_check_output.return_value = b'mock_output'
        mock_getoutput.return_value = 'mock_output'

        yield {
            'popen': mock_popen,
            'call': mock_call,
            'check_output': mock_check_output,
            'getoutput': mock_getoutput,
            'process': mock_process
        }

@pytest.fixture(scope='function')
def mock_os_system():
    """Mock os.system calls"""
    with patch('os.system') as mock_system:
        mock_system.return_value = 0
        yield mock_system

@pytest.fixture(scope='function')
def mock_requests():
    """Mock requests library"""
    with patch('requests.get') as mock_get, \
         patch('requests.post') as mock_post, \
         patch('requests.put') as mock_put, \
         patch('requests.delete') as mock_delete:

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"success": true}'
        mock_response.json.return_value = {'success': True}

        mock_get.return_value = mock_response
        mock_post.return_value = mock_response
        mock_put.return_value = mock_response
        mock_delete.return_value = mock_response

        yield {
            'get': mock_get,
            'post': mock_post,
            'put': mock_put,
            'delete': mock_delete,
            'response': mock_response
        }

@pytest.fixture(scope='function')
def mock_docker():
    """Mock Docker operations"""
    with patch('subprocess.Popen') as mock_popen:
        mock_process = MagicMock()
        mock_process.communicate.return_value = (b'true', b'')
        mock_popen.return_value = mock_process
        yield mock_popen

@pytest.fixture(scope='function')
def sample_model_data():
    """Sample model data for testing"""
    return {
        'model_name': 'test_model',
        'version': 'v1.0.0',
        'labelmap_dict': {
            'class1': 1,
            'class2': 2,
            'class3': 3
        }
    }

@pytest.fixture(scope='function')
def sample_network_data():
    """Sample network configuration data"""
    return {
        'ip': '192.168.1.100',
        'lanPort': 'enp0s31f6',
        'dhcp': False
    }

@pytest.fixture(scope='function')
def mock_file_operations():
    """Mock file operations"""
    with patch('builtins.open', create=True) as mock_open:
        mock_file = MagicMock()
        mock_file.read.return_value = '{"test": "data"}'
        mock_file.__enter__.return_value = mock_file
        mock_open.return_value = mock_file
        yield mock_open

@pytest.fixture(scope='function')
def mock_auth():
    """Mock authentication decorator"""
    def mock_requires_auth(f):
        return f

    with patch('auth.requires_auth', side_effect=mock_requires_auth):
        yield

@pytest.fixture(scope='function')
def temp_test_dir(tmp_path):
    """Create temporary directory for file operations"""
    test_dir = tmp_path / "test_data"
    test_dir.mkdir()
    return str(test_dir)

@pytest.fixture(autouse=True)
def reset_environment():
    """Reset environment between tests"""
    yield
    # Cleanup code here if needed

@pytest.fixture(scope='function')
def mock_platform():
    """Mock platform processor"""
    with patch('platform.processor') as mock_proc:
        mock_proc.return_value = 'x86_64'
        yield mock_proc

@pytest.fixture(scope='function')
def fake_redis_with_data():
    """Fake Redis with pre-populated test data"""
    import fakeredis
    fake_redis = fakeredis.FakeStrictRedis()

    # Pre-populate with test data
    fake_redis.set('test_key', 'test_value')
    fake_redis.hset('test_hash', 'field1', 'value1')
    fake_redis.lpush('test_list', 'item1', 'item2')

    yield fake_redis

    # Cleanup
    fake_redis.flushall()

@pytest.fixture(scope='function')
def fake_redis_server():
    """Fake Redis server for more complex testing scenarios"""
    import fakeredis
    server = fakeredis.FakeServer()
    fake_redis = fakeredis.FakeStrictRedis(server=server)

    with patch('redis.Redis', return_value=fake_redis):
        yield {'server': server, 'redis': fake_redis}

    # Cleanup
    fake_redis.flushall()
