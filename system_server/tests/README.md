# FlexRun System Server Testing Suite

Comprehensive testing documentation for the FlexRun System Server application.

## Table of Contents

- [Overview](#overview)
- [Test Structure](#test-structure)
- [Setup](#setup)
- [Running Tests](#running-tests)
- [Test Categories](#test-categories)
- [Writing Tests](#writing-tests)
- [Coverage Reports](#coverage-reports)
- [CI/CD Integration](#cicd-integration)
- [Best Practices](#best-practices)

## Overview

The testing suite provides comprehensive coverage for the FlexRun System Server, including:

- **Unit Tests**: Testing individual functions and utilities in isolation
- **Integration Tests**: Testing API endpoints and component interactions
- **Fixtures**: Reusable test data and mocking utilities
- **Coverage Reporting**: Detailed code coverage analysis

### Testing Framework

- **pytest**: Primary testing framework
- **pytest-cov**: Coverage reporting
- **pytest-mock**: Enhanced mocking capabilities
- **pytest-flask**: Flask-specific testing utilities
- **fakeredis**: In-memory fake Redis for testing (no Redis server required)
- **mongomock**: In-memory fake MongoDB for testing

## Test Structure

```
tests/
├── README.md                    # This file
├── conftest.py                  # Shared fixtures and configuration
├── unit/                        # Unit tests
│   ├── test_network_utils.py   # Network utility tests
│   ├── test_device_utils.py    # Device utility tests
│   └── ...
├── integration/                 # Integration tests
│   ├── test_system_routes.py   # System management endpoint tests
│   ├── test_network_routes.py  # Network configuration endpoint tests
│   ├── test_model_routes.py    # Model management endpoint tests
│   ├── test_device_routes.py   # Device/hardware endpoint tests
│   └── ...
└── fixtures/                    # Test data and fixtures
    └── ...
```

## Setup

### 1. Install Test Dependencies

```bash
pip install -r test_requirements.txt
```

### 2. Verify Installation

```bash
pytest --version
```

### 3. Configure Test Environment

The test suite uses mocked configurations by default. To customize:

1. Copy `conftest.py` and modify fixtures as needed
2. Set environment variables if required
3. Ensure MongoDB and Redis are NOT required for tests (mocked by default)

## Running Tests

### Quick Start

Run all tests:

```bash
./run_tests.sh
```

Or using pytest directly:

```bash
pytest
```

### Specific Test Categories

**Unit tests only:**
```bash
./run_tests.sh unit
# OR
pytest -m unit
```

**Integration tests only:**
```bash
./run_tests.sh integration
# OR
pytest -m integration
```

**Quick tests (exclude slow):**
```bash
./run_tests.sh quick
# OR
pytest -m "not slow"
```

### Specific Test Files

```bash
./run_tests.sh file tests/unit/test_network_utils.py
# OR
pytest tests/unit/test_network_utils.py
```

### Specific Test Class or Function

```bash
pytest tests/unit/test_network_utils.py::TestIPValidation
pytest tests/unit/test_network_utils.py::TestIPValidation::test_is_valid_ip_valid
```

### With Coverage

```bash
./run_tests.sh coverage
# OR
pytest --cov=routes --cov=utils --cov-report=html
```

View coverage report:
```bash
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
```

### Watch Mode

Auto-run tests when files change:

```bash
./run_tests.sh watch
```

## Test Categories

Tests are organized using pytest markers:

### Available Markers

- `@pytest.mark.unit` - Unit tests for individual functions
- `@pytest.mark.integration` - Integration tests for API endpoints
- `@pytest.mark.slow` - Tests that take longer to run
- `@pytest.mark.network` - Tests requiring network access
- `@pytest.mark.database` - Tests requiring database access
- `@pytest.mark.auth` - Tests involving authentication
- `@pytest.mark.gpio` - Tests for GPIO functionality
- `@pytest.mark.docker` - Tests interacting with Docker

### Running by Marker

```bash
pytest -m network          # Run only network tests
pytest -m "unit and not slow"  # Run fast unit tests
pytest -m "integration or network"  # Run integration OR network tests
```

## Writing Tests

### Basic Test Structure

```python
import pytest
from unittest.mock import patch, MagicMock

class TestMyFeature:
    """Tests for my feature"""

    @pytest.mark.unit
    def test_basic_functionality(self):
        """Test basic functionality"""
        # Arrange
        input_data = "test"

        # Act
        result = my_function(input_data)

        # Assert
        assert result == "expected"
```

### Using Fixtures

```python
@pytest.mark.integration
def test_with_client(client):
    """Test using the Flask test client"""
    response = client.get('/endpoint')
    assert response.status_code == 200
```

### Common Fixtures Available

From `conftest.py`:

- `client` - Flask test client
- `mock_mongo_client` - Mocked MongoDB
- `mock_redis` - Fake Redis instance using fakeredis (supports all Redis commands)
- `fake_redis_with_data` - Fake Redis pre-populated with test data
- `fake_redis_server` - Fake Redis server for shared instance testing
- `mock_job_queue` - Mocked RQ queue with fakeredis backend
- `auth_headers` - Authentication headers
- `mock_subprocess` - Mocked subprocess calls
- `mock_os_system` - Mocked os.system
- `mock_requests` - Mocked HTTP requests
- `mock_auth` - Bypass authentication

### Mocking Examples

**Using fakeredis for Redis operations:**
```python
@pytest.mark.unit
def test_redis_operations(mock_redis):
    """Test using fakeredis fixture"""
    # Set and get values
    mock_redis.set('key', 'value')
    assert mock_redis.get('key') == b'value'

    # Use Redis lists
    mock_redis.lpush('mylist', 'item1', 'item2')
    assert mock_redis.llen('mylist') == 2

    # Use Redis hashes
    mock_redis.hset('user:1', 'name', 'John')
    assert mock_redis.hget('user:1', 'name') == b'John'

@pytest.mark.unit
def test_with_preloaded_redis(fake_redis_with_data):
    """Test using pre-populated Redis data"""
    # Data is already available
    assert fake_redis_with_data.get('test_key') == b'test_value'
    assert fake_redis_with_data.hget('test_hash', 'field1') == b'value1'

@pytest.mark.unit
def test_shared_redis_server(fake_redis_server):
    """Test with shared Redis server instance"""
    import fakeredis
    server = fake_redis_server['server']
    redis1 = fake_redis_server['redis']

    # Create another connection to same server
    redis2 = fakeredis.FakeStrictRedis(server=server)

    # Data is shared across connections
    redis1.set('shared', 'data')
    assert redis2.get('shared') == b'data'
```

**Mock subprocess:**
```python
@patch('subprocess.Popen')
def test_with_subprocess(mock_popen):
    mock_process = MagicMock()
    mock_process.communicate.return_value = (b'output', b'')
    mock_popen.return_value = mock_process

    result = function_that_uses_subprocess()
    assert result == 'expected'
```

**Mock external API:**
```python
@patch('requests.get')
def test_api_call(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {'data': 'value'}
    mock_get.return_value = mock_response

    result = function_that_calls_api()
    assert result['data'] == 'value'
```

**Mock file operations:**
```python
from unittest.mock import mock_open

@patch('builtins.open', new_callable=mock_open, read_data='file content')
def test_file_read(mock_file):
    result = function_that_reads_file()
    assert 'file content' in result
```

### Testing API Endpoints

```python
@pytest.mark.integration
@patch('auth.requires_auth', lambda f: f)  # Bypass auth
def test_endpoint(client_with_routes):
    """Test API endpoint"""
    # GET request
    response = client_with_routes.get('/endpoint')
    assert response.status_code == 200

    # POST request
    data = {'key': 'value'}
    response = client_with_routes.post(
        '/endpoint',
        data=json.dumps(data),
        content_type='application/json'
    )
    assert response.status_code == 201
```

## Coverage Reports

### Generate Coverage Report

```bash
pytest --cov=routes --cov=utils --cov-report=html --cov-report=term
```

### Coverage Goals

Target coverage levels:
- **Overall**: > 80%
- **Utils modules**: > 90%
- **Route handlers**: > 75%
- **Critical paths**: 100%

### Viewing Coverage

**Terminal output:**
```bash
pytest --cov=routes --cov=utils --cov-report=term-missing
```

**HTML report:**
```bash
pytest --cov=routes --cov=utils --cov-report=html
open htmlcov/index.html
```

**XML report (for CI/CD):**
```bash
pytest --cov=routes --cov=utils --cov-report=xml
```

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
    - uses: actions/checkout@v2

    - name: Set up Python
      uses: actions/setup-python@v2
      with:
        python-version: '3.8'

    - name: Install dependencies
      run: |
        pip install -r requirements.txt
        pip install -r test_requirements.txt

    - name: Run tests
      run: |
        pytest --cov=routes --cov=utils --cov-report=xml

    - name: Upload coverage
      uses: codecov/codecov-action@v2
```

### GitLab CI Example

```yaml
test:
  image: python:3.8
  script:
    - pip install -r requirements.txt
    - pip install -r test_requirements.txt
    - pytest --cov=routes --cov=utils --cov-report=term --cov-report=xml
  coverage: '/TOTAL.*\s+(\d+%)$/'
  artifacts:
    reports:
      coverage_report:
        coverage_format: cobertura
        path: coverage.xml
```

## Best Practices

### 1. Test Naming

- Use descriptive names: `test_get_valid_ip_address_returns_true`
- Follow pattern: `test_<function>_<scenario>_<expected_result>`
- Use docstrings to explain complex tests

### 2. Test Organization

- Group related tests in classes
- One test class per feature/function
- Keep tests focused and small

### 3. Assertions

- Use specific assertions: `assert x == 5` not `assert x`
- Include assertion messages: `assert x == 5, f"Expected 5, got {x}"`
- Test one thing per test method

### 4. Mocking

- Mock external dependencies (network, filesystem, database)
- Don't mock the code under test
- Use fixtures for common mocks

### 5. Test Data

- Use fixtures for test data
- Keep test data minimal and relevant
- Don't use production data in tests

### 6. Isolation

- Tests should not depend on each other
- Clean up after tests (use fixtures with yield)
- Don't rely on execution order

### 7. Performance

- Keep unit tests fast (< 100ms each)
- Mark slow tests with `@pytest.mark.slow`
- Run slow tests separately in CI

### 8. Coverage

- Aim for high coverage but prioritize critical paths
- Don't chase 100% coverage at the expense of test quality
- Focus on testing behavior, not implementation

## Troubleshooting

### Common Issues

**Import errors:**
```bash
# Ensure system_server is in Python path
export PYTHONPATH=/path/to/onpremflexrun/system_server:$PYTHONPATH
```

**MongoDB/Redis connection errors:**
- Tests use fakeredis and mongomock (no real database required)
- fakeredis provides a full in-memory Redis implementation
- No Redis server needs to be running for tests
- All Redis commands are supported by fakeredis
- Ensure fixtures are properly imported from conftest.py

**Authentication failures:**
- Use `@patch('auth.requires_auth', lambda f: f)` to bypass
- Use `auth_headers` fixture for authenticated requests

**Fixture not found:**
```python
# Import from conftest
pytest tests/  # Run from tests directory
```

### Debug Mode

Run tests with verbose output and show print statements:

```bash
pytest -vv -s
```

Run tests with step-by-step debugging:

```bash
pytest --pdb  # Drop into debugger on failure
pytest --trace  # Start debugger at beginning of test
```

## Additional Resources

- [pytest documentation](https://docs.pytest.org/)
- [pytest-flask documentation](https://pytest-flask.readthedocs.io/)
- [unittest.mock documentation](https://docs.python.org/3/library/unittest.mock.html)
- [Coverage.py documentation](https://coverage.readthedocs.io/)

## Contributing

When adding new features:

1. Write tests first (TDD approach recommended)
2. Ensure all tests pass: `./run_tests.sh`
3. Check coverage: `./run_tests.sh coverage`
4. Add appropriate markers
5. Update this README if adding new test categories

## Support

For issues or questions about the testing suite:

1. Check this README
2. Review existing tests for examples
3. Check pytest documentation
4. Open an issue in the project repository
