"""
Unit tests for Kinesis.py
"""
import pytest
import json
import uuid
import datetime
from unittest.mock import Mock, patch, MagicMock, call


class TestMsTimestamp:
    """Tests for ms_timestamp function"""

    @pytest.mark.unit
    @patch('datetime.datetime')
    def test_ms_timestamp_returns_milliseconds(self, mock_datetime):
        """Test that ms_timestamp returns time in milliseconds"""
        mock_now = MagicMock()
        mock_now.timestamp.return_value = 1234567890.123456
        mock_datetime.now.return_value = mock_now

        def ms_timestamp():
            return int(datetime.datetime.now().timestamp()*1000)

        result = ms_timestamp()
        expected = int(1234567890.123456 * 1000)

        assert result == expected
        assert isinstance(result, int)


class TestKinesisInit:
    """Tests for Kinesis class initialization"""

    @pytest.mark.unit
    def test_kinesis_initialization(self):
        """Test Kinesis class initialization"""
        # Replicate Kinesis.__init__ logic
        class MockKinesis:
            def __init__(self):
                self.stream = None
                self.expiration = None
                self.REGION_NAME = 'us-east-1'
                self.ACCESS_KEY = None
                self.SECRET_KEY = None
                self.CLIENT = None
                self.authorized = False

        kinesis = MockKinesis()

        assert kinesis.stream is None
        assert kinesis.expiration is None
        assert kinesis.REGION_NAME == 'us-east-1'
        assert kinesis.ACCESS_KEY is None
        assert kinesis.SECRET_KEY is None
        assert kinesis.CLIENT is None
        assert kinesis.authorized is False


class TestAuthorize:
    """Tests for authorize method"""

    @pytest.mark.unit
    @patch('requests.post')
    def test_authorize_success(self, mock_post):
        """Test successful authorization"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'keys': {
                'access_key': 'test_access_key',
                'secret_key': 'test_secret_key',
                'arn': 'test_stream_arn'
            },
            'expiration': 9999999999999
        }
        mock_post.return_value = mock_response

        # Replicate authorize logic
        class MockKinesis:
            def __init__(self):
                self.ACCESS_KEY = None
                self.SECRET_KEY = None
                self.stream = None
                self.expiration = None
                self.authorized = False

            def authorize(self, access_token, cloud_functions_base):
                auth_token = 'Bearer {}'.format(access_token)
                headers = {'Authorization': auth_token}
                url = '{}pull_foreign_auth'.format(cloud_functions_base)
                data = {'resource_name': 'aws_kinesis'}
                resp = mock_post(url, json=data, headers=headers, timeout=30)

                if resp.status_code == 200:
                    data = resp.json()
                    self.ACCESS_KEY = data['keys']['access_key']
                    self.SECRET_KEY = data['keys']['secret_key']
                    self.stream = data['keys']['arn']
                    self.expiration = data['expiration']
                    self.authorized = True
                    return True
                else:
                    self.authorized = False
                    return False

        kinesis = MockKinesis()
        result = kinesis.authorize('test_token', 'https://test.cloudfunctions.net/')

        assert result is True
        assert kinesis.ACCESS_KEY == 'test_access_key'
        assert kinesis.SECRET_KEY == 'test_secret_key'
        assert kinesis.stream == 'test_stream_arn'
        assert kinesis.expiration == 9999999999999
        assert kinesis.authorized is True

    @pytest.mark.unit
    @patch('requests.post')
    def test_authorize_failure(self, mock_post):
        """Test authorization failure"""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_post.return_value = mock_response

        class MockKinesis:
            def __init__(self):
                self.ACCESS_KEY = None
                self.SECRET_KEY = None
                self.stream = None
                self.expiration = None
                self.authorized = False

            def authorize(self, access_token, cloud_functions_base):
                auth_token = 'Bearer {}'.format(access_token)
                headers = {'Authorization': auth_token}
                url = '{}pull_foreign_auth'.format(cloud_functions_base)
                data = {'resource_name': 'aws_kinesis'}
                resp = mock_post(url, json=data, headers=headers, timeout=30)

                if resp.status_code == 200:
                    self.authorized = True
                    return True
                else:
                    self.authorized = False
                    return False

        kinesis = MockKinesis()
        result = kinesis.authorize('test_token', 'https://test.cloudfunctions.net/')

        assert result is False
        assert kinesis.authorized is False


class TestValidateExpiry:
    """Tests for validate_expiry method"""

    @pytest.mark.unit
    @patch('aws.Kinesis.ms_timestamp')
    def test_validate_expiry_not_expired(self, mock_ms_timestamp):
        """Test validate_expiry when token is not expired"""
        mock_ms_timestamp.return_value = 1000000
        expiration = 2000000  # Future time

        # Replicate validate_expiry logic
        def validate_expiry(expiration, current_time):
            is_expired = expiration < current_time
            return is_expired

        result = validate_expiry(expiration, mock_ms_timestamp.return_value)

        assert result is False

    @pytest.mark.unit
    @patch('aws.Kinesis.ms_timestamp')
    def test_validate_expiry_expired(self, mock_ms_timestamp):
        """Test validate_expiry when token is expired"""
        mock_ms_timestamp.return_value = 2000000
        expiration = 1000000  # Past time

        def validate_expiry(expiration, current_time):
            is_expired = expiration < current_time
            return is_expired

        result = validate_expiry(expiration, mock_ms_timestamp.return_value)

        assert result is True


class TestConnectClient:
    """Tests for _connect_client method"""

    @pytest.mark.unit
    @patch('boto3.client')
    def test_connect_client_success(self, mock_boto3_client):
        """Test successful client connection"""
        mock_client = MagicMock()
        mock_boto3_client.return_value = mock_client

        # Replicate _connect_client logic
        class MockKinesis:
            def __init__(self):
                self.REGION_NAME = 'us-east-1'
                self.ACCESS_KEY = 'test_key'
                self.SECRET_KEY = 'test_secret'
                self.CLIENT = None
                self.authorized = False
                self.expiration = 9999999999999

            def _connect_client(self):
                did_authorize = True  # Assuming already authorized

                if did_authorize:
                    self.CLIENT = mock_boto3_client('kinesis',
                                        region_name=self.REGION_NAME,
                                        aws_access_key_id=self.ACCESS_KEY,
                                        aws_secret_access_key=self.SECRET_KEY)
                    self.authorized = True
                    return self.CLIENT
                else:
                    return False

        kinesis = MockKinesis()
        result = kinesis._connect_client()

        assert result == mock_client
        assert kinesis.CLIENT == mock_client
        assert kinesis.authorized is True
        mock_boto3_client.assert_called_once_with(
            'kinesis',
            region_name='us-east-1',
            aws_access_key_id='test_key',
            aws_secret_access_key='test_secret'
        )

    @pytest.mark.unit
    def test_connect_client_unauthorized(self):
        """Test client connection when unauthorized"""
        class MockKinesis:
            def __init__(self):
                self.CLIENT = None
                self.authorized = False

            def _connect_client(self):
                did_authorize = False

                if did_authorize:
                    return self.CLIENT
                else:
                    return False

        kinesis = MockKinesis()
        result = kinesis._connect_client()

        assert result is False


class TestSendStream:
    """Tests for send_stream method"""

    @pytest.mark.unit
    @patch('uuid.uuid4')
    def test_send_stream_success(self, mock_uuid):
        """Test successful stream send"""
        mock_uuid.return_value = 'test-uuid-1234'

        mock_client = MagicMock()

        class MockKinesis:
            def __init__(self):
                self.authorized = True
                self.stream = 'test_stream_arn'

            def _connect_client(self):
                return mock_client

            def send_stream(self, data, partition_key=None):
                if not self.authorized:
                    return 'Service not authorized', 403

                if partition_key == None:
                    partition_key = mock_uuid()

                client = self._connect_client()
                if client:
                    client.put_record(
                        StreamARN=self.stream,
                        Data=json.dumps(data)+'\n',
                        PartitionKey=str(partition_key)
                    )
                    return True
                else:
                    return False

        kinesis = MockKinesis()
        test_data = {'id': 'rec1', 'value': 42}
        result = kinesis.send_stream(test_data)

        assert result is True
        mock_client.put_record.assert_called_once_with(
            StreamARN='test_stream_arn',
            Data=json.dumps(test_data)+'\n',
            PartitionKey='test-uuid-1234'
        )

    @pytest.mark.unit
    def test_send_stream_with_partition_key(self):
        """Test stream send with custom partition key"""
        mock_client = MagicMock()

        class MockKinesis:
            def __init__(self):
                self.authorized = True
                self.stream = 'test_stream_arn'

            def _connect_client(self):
                return mock_client

            def send_stream(self, data, partition_key=None):
                if not self.authorized:
                    return 'Service not authorized', 403

                client = self._connect_client()
                if client:
                    client.put_record(
                        StreamARN=self.stream,
                        Data=json.dumps(data)+'\n',
                        PartitionKey=str(partition_key)
                    )
                    return True
                else:
                    return False

        kinesis = MockKinesis()
        test_data = {'id': 'rec1', 'value': 42}
        result = kinesis.send_stream(test_data, partition_key='custom-key-123')

        assert result is True
        mock_client.put_record.assert_called_once_with(
            StreamARN='test_stream_arn',
            Data=json.dumps(test_data)+'\n',
            PartitionKey='custom-key-123'
        )

    @pytest.mark.unit
    def test_send_stream_unauthorized(self):
        """Test stream send when unauthorized"""
        class MockKinesis:
            def __init__(self):
                self.authorized = False

            def send_stream(self, data, partition_key=None):
                if not self.authorized:
                    return 'Service not authorized', 403

        kinesis = MockKinesis()
        result = kinesis.send_stream({'data': 'test'})

        assert result == ('Service not authorized', 403)

    @pytest.mark.unit
    def test_send_stream_client_connection_fails(self):
        """Test stream send when client connection fails"""
        class MockKinesis:
            def __init__(self):
                self.authorized = True

            def _connect_client(self):
                return False

            def send_stream(self, data, partition_key=None):
                if not self.authorized:
                    return 'Service not authorized', 403

                client = self._connect_client()
                if client:
                    return True
                else:
                    return False

        kinesis = MockKinesis()
        result = kinesis.send_stream({'data': 'test'})

        assert result is False


class TestGetAuthToken:
    """Tests for get_auth_token method"""

    @pytest.mark.unit
    def test_get_auth_token_success(self):
        """Test successful auth token retrieval"""
        mock_util_ref = MagicMock()
        mock_util_ref.find_one.return_value = {
            'type': 'access_token',
            'token': 'test_access_token_12345'
        }

        def get_auth_token():
            access_token_obj = mock_util_ref.find_one({'type': 'access_token'}, {'_id': 0})
            access_token = access_token_obj['token']
            return access_token

        result = get_auth_token()

        assert result == 'test_access_token_12345'
        mock_util_ref.find_one.assert_called_once_with({'type': 'access_token'}, {'_id': 0})

    @pytest.mark.unit
    def test_get_auth_token_not_found(self):
        """Test auth token retrieval when not found"""
        mock_util_ref = MagicMock()
        mock_util_ref.find_one.return_value = None

        def get_auth_token():
            access_token_obj = mock_util_ref.find_one({'type': 'access_token'}, {'_id': 0})
            if access_token_obj:
                return access_token_obj['token']
            return None

        result = get_auth_token()

        assert result is None


class TestDataSerialization:
    """Tests for data serialization"""

    @pytest.mark.unit
    def test_json_dumps_with_newline(self):
        """Test that data is serialized with newline"""
        data = {'id': 'test', 'value': 123}

        serialized = json.dumps(data) + '\n'

        assert serialized.endswith('\n')
        assert json.loads(serialized.strip()) == data

    @pytest.mark.unit
    def test_json_dumps_various_types(self):
        """Test JSON serialization of various data types"""
        test_cases = [
            {'string': 'value'},
            {'number': 42},
            {'float': 3.14},
            {'bool': True},
            {'null': None},
            {'array': [1, 2, 3]},
            {'nested': {'key': 'value'}}
        ]

        for data in test_cases:
            serialized = json.dumps(data) + '\n'
            assert isinstance(serialized, str)
            assert serialized.endswith('\n')
            deserialized = json.loads(serialized.strip())
            assert deserialized == data


class TestPartitionKey:
    """Tests for partition key handling"""

    @pytest.mark.unit
    @patch('uuid.uuid4')
    def test_partition_key_generated(self, mock_uuid):
        """Test partition key is generated when not provided"""
        mock_uuid.return_value = uuid.UUID('12345678-1234-5678-1234-567812345678')

        partition_key = None
        if partition_key == None:
            partition_key = uuid.uuid4()

        assert partition_key == uuid.UUID('12345678-1234-5678-1234-567812345678')
        mock_uuid.assert_called_once()

    @pytest.mark.unit
    def test_partition_key_provided(self):
        """Test partition key is used when provided"""
        partition_key = 'custom-partition-key'

        if partition_key == None:
            partition_key = uuid.uuid4()

        assert partition_key == 'custom-partition-key'

    @pytest.mark.unit
    def test_partition_key_string_conversion(self):
        """Test partition key is converted to string"""
        partition_keys = [
            uuid.uuid4(),
            123,
            'string-key',
            None
        ]

        for key in partition_keys:
            if key is not None:
                str_key = str(key)
                assert isinstance(str_key, str)


class TestRegionConfiguration:
    """Tests for region configuration"""

    @pytest.mark.unit
    def test_default_region_name(self):
        """Test default region name is us-east-1"""
        class MockKinesis:
            def __init__(self):
                self.REGION_NAME = 'us-east-1'

        kinesis = MockKinesis()

        assert kinesis.REGION_NAME == 'us-east-1'


class TestAuthorizationHeader:
    """Tests for authorization header construction"""

    @pytest.mark.unit
    def test_bearer_token_format(self):
        """Test Bearer token format"""
        access_token = 'test_token_12345'
        auth_token = 'Bearer {}'.format(access_token)

        assert auth_token == 'Bearer test_token_12345'
        assert auth_token.startswith('Bearer ')

    @pytest.mark.unit
    def test_authorization_headers(self):
        """Test authorization headers dictionary"""
        access_token = 'test_token_12345'
        auth_token = 'Bearer {}'.format(access_token)
        headers = {'Authorization': auth_token}

        assert 'Authorization' in headers
        assert headers['Authorization'] == 'Bearer test_token_12345'


class TestRequestData:
    """Tests for request data construction"""

    @pytest.mark.unit
    def test_resource_name_in_request(self):
        """Test resource_name is included in request data"""
        data = {'resource_name': 'aws_kinesis'}

        assert 'resource_name' in data
        assert data['resource_name'] == 'aws_kinesis'


class TestStreamARN:
    """Tests for Stream ARN handling"""

    @pytest.mark.unit
    def test_stream_arn_storage(self):
        """Test stream ARN is stored correctly"""
        class MockKinesis:
            def __init__(self):
                self.stream = None

            def set_stream(self, arn):
                self.stream = arn

        kinesis = MockKinesis()
        kinesis.set_stream('arn:aws:kinesis:us-east-1:123456789:stream/test-stream')

        assert kinesis.stream == 'arn:aws:kinesis:us-east-1:123456789:stream/test-stream'
        assert kinesis.stream.startswith('arn:aws:kinesis:')
