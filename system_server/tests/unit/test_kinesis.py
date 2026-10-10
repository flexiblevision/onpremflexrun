"""AWS Kinesis credential handling and record publishing.

The previous version of this file defined copies of the functions inside each
test body, so Kinesis.py itself was never imported and sat at 13.8% while 24
tests reported green.

The behaviour that matters is credential lifecycle. Credentials are short-lived
and shared between processes through mongo: refreshing too late puts records
through an expired key, refreshing on every call hammers the auth endpoint, and
retrying a hard auth failure in a tight loop does both.
"""
import datetime
import json
import threading
import pytest
from unittest.mock import patch, MagicMock, call

import botocore.exceptions

from aws import Kinesis as kinesis_module
from aws.Kinesis import Kinesis


NOW_MS = 1_700_000_000_000
HOUR_MS = 60 * 60 * 1000
REFRESH_BUFFER_MS = 5 * 60 * 1000


@pytest.fixture(autouse=True)
def frozen_clock():
    patcher = patch.object(kinesis_module, 'ms_timestamp', return_value=NOW_MS)
    patcher.start()
    yield patcher
    patcher.stop()


@pytest.fixture
def credentials_cache():
    """The mongo collection Kinesis shares credentials through."""
    collection = MagicMock()
    collection.find_one.return_value = None
    with patch.object(kinesis_module, 'kinesis_log') as log:
        log.database.__getitem__.return_value = collection
        yield collection


@pytest.fixture
def auth_token():
    with patch.object(kinesis_module, 'util_ref') as util:
        util.find_one.return_value = {'token': 'id-token'}
        yield util


def _auth_response(status=200, expiration=NOW_MS + HOUR_MS):
    response = MagicMock(status_code=status, text='')
    response.json.return_value = {
        'keys': {'access_key': 'AKIA', 'secret_key': 'SECRET', 'arn': 'arn:stream'},
        'expiration': expiration,
    }
    return response


@pytest.fixture
def unauthorized():
    """A Kinesis instance whose constructor authorize() was suppressed."""
    with patch.object(Kinesis, 'authorize', return_value=False):
        instance = Kinesis()
    return instance


class TestMsTimestamp:
    @pytest.mark.unit
    def test_is_epoch_milliseconds(self, frozen_clock):
        # Every expiry comparison in this module is against this value, so it
        # has to be in the same units as the 'expiration' the cloud returns.
        frozen_clock.stop()
        try:
            value = kinesis_module.ms_timestamp()
        finally:
            frozen_clock.start()

        expected = datetime.datetime.now().timestamp() * 1000
        assert isinstance(value, int)
        assert abs(value - expected) < 5000


class TestConstruction:
    @pytest.mark.unit
    def test_authorizes_on_construction(self, credentials_cache, auth_token):
        with patch.object(Kinesis, 'authorize') as authorize:
            Kinesis()
        authorize.assert_called_once()

    @pytest.mark.unit
    def test_a_failed_authorization_does_not_prevent_construction(self):
        # settings imports Kinesis at startup; raising here would take the
        # whole server down on a device with no network.
        with patch.object(Kinesis, 'authorize', side_effect=RuntimeError('offline')):
            instance = Kinesis()

        assert instance.authorized is False

    @pytest.mark.unit
    def test_defaults(self, unauthorized):
        assert unauthorized.REGION_NAME == 'us-east-1'
        assert unauthorized.CLIENT is None
        assert unauthorized.AUTH_RETRY_MS == 60_000


class TestAuthorize:
    @pytest.mark.unit
    def test_valid_cached_credentials_are_reused(self, credentials_cache, unauthorized):
        credentials_cache.find_one.return_value = {
            'access_key': 'CACHED', 'secret_key': 'CSECRET', 'arn': 'arn:cached',
            'expiration': NOW_MS + HOUR_MS,
        }

        with patch('requests.post') as post:
            assert unauthorized.authorize() is True

        # No round trip to the cloud: other processes on the device share this.
        post.assert_not_called()
        assert unauthorized.ACCESS_KEY == 'CACHED'
        assert unauthorized.stream == 'arn:cached'
        assert unauthorized.authorized is True

    @pytest.mark.unit
    def test_cached_credentials_inside_the_refresh_buffer_are_not_reused(
            self, credentials_cache, auth_token, unauthorized):
        # Expiring in 4 minutes: too close to hand to a long-running request.
        credentials_cache.find_one.return_value = {
            'access_key': 'CACHED', 'secret_key': 'C', 'arn': 'a',
            'expiration': NOW_MS + (4 * 60 * 1000),
        }

        with patch('requests.post', return_value=_auth_response()) as post:
            assert unauthorized.authorize() is True

        post.assert_called_once()
        assert unauthorized.ACCESS_KEY == 'AKIA'

    @pytest.mark.unit
    def test_expired_cached_credentials_are_not_reused(
            self, credentials_cache, auth_token, unauthorized):
        credentials_cache.find_one.return_value = {
            'access_key': 'CACHED', 'secret_key': 'C', 'arn': 'a',
            'expiration': NOW_MS - HOUR_MS,
        }

        with patch('requests.post', return_value=_auth_response()):
            unauthorized.authorize()

        assert unauthorized.ACCESS_KEY == 'AKIA'

    @pytest.mark.unit
    def test_a_cache_entry_without_an_expiry_is_not_reused(
            self, credentials_cache, auth_token, unauthorized):
        credentials_cache.find_one.return_value = {'access_key': 'CACHED'}

        with patch('requests.post', return_value=_auth_response()) as post:
            unauthorized.authorize()

        post.assert_called_once()

    @pytest.mark.unit
    def test_fetches_credentials_with_the_device_token(
            self, credentials_cache, auth_token, unauthorized):
        with patch('requests.post', return_value=_auth_response()) as post:
            assert unauthorized.authorize() is True

        assert post.call_args[0][0] == kinesis_module.FOREIGN_PULL_PATH
        assert post.call_args[1]['headers'] == {'Authorization': 'Bearer id-token'}
        assert post.call_args[1]['json'] == {'resource_name': 'aws_kinesis'}
        assert post.call_args[1]['timeout'] == 30

    @pytest.mark.unit
    def test_fresh_credentials_are_cached_for_other_processes(
            self, credentials_cache, auth_token, unauthorized):
        with patch('requests.post', return_value=_auth_response()):
            unauthorized.authorize()

        query, update = credentials_cache.update_one.call_args[0]
        assert query == {'_id': 'aws_kinesis'}
        assert update['$set']['access_key'] == 'AKIA'
        assert update['$set']['arn'] == 'arn:stream'
        assert credentials_cache.update_one.call_args[1]['upsert'] is True

    @pytest.mark.unit
    def test_a_rejected_request_records_the_failure_time(
            self, credentials_cache, auth_token, unauthorized):
        with patch('requests.post', return_value=_auth_response(status=403)):
            assert unauthorized.authorize() is False

        assert unauthorized.authorized is False
        assert unauthorized._last_auth_failure == NOW_MS

    @pytest.mark.unit
    def test_a_successful_authorization_clears_a_previous_failure(
            self, credentials_cache, auth_token, unauthorized):
        unauthorized._last_auth_failure = NOW_MS - 1000

        with patch('requests.post', return_value=_auth_response()):
            unauthorized.authorize()

        assert unauthorized._last_auth_failure is None

    @pytest.mark.unit
    def test_debug_logging_is_off_by_default(self, credentials_cache, auth_token,
                                             unauthorized):
        with patch('requests.post', return_value=_auth_response()), \
             patch.object(unauthorized, '_log') as log:
            unauthorized.authorize()
        log.assert_not_called()

    @pytest.mark.unit
    def test_debug_logging_records_the_outcome(self, credentials_cache, auth_token,
                                               unauthorized):
        unauthorized.debug = True
        with patch('requests.post', return_value=_auth_response()), \
             patch.object(unauthorized, '_log') as log:
            unauthorized.authorize()

        assert log.call_args[0][0] == 'authorize_success'


class TestValidateExpiry:
    @pytest.mark.unit
    def test_an_expired_credential_is_refreshed(self, unauthorized):
        unauthorized.expiration = NOW_MS - 1

        with patch.object(unauthorized, 'authorize') as authorize:
            unauthorized.validate_expiry()

        authorize.assert_called_once()

    @pytest.mark.unit
    def test_a_live_credential_is_left_alone(self, unauthorized):
        unauthorized.expiration = NOW_MS + HOUR_MS

        with patch.object(unauthorized, 'authorize') as authorize:
            unauthorized.validate_expiry()

        authorize.assert_not_called()

    @pytest.mark.unit
    def test_an_unauthorized_instance_raises(self, unauthorized):
        # expiration is None until the first successful authorize; the sync
        # worker calls this every cycle, so a device that has never authorized
        # takes the loop down.
        unauthorized.expiration = None

        with pytest.raises(TypeError):
            unauthorized.validate_expiry()


class TestConnectClient:
    @pytest.mark.unit
    def test_a_live_credential_builds_a_client_without_reauthorizing(
            self, unauthorized):
        unauthorized.expiration = NOW_MS + HOUR_MS
        unauthorized.ACCESS_KEY = 'AKIA'
        unauthorized.SECRET_KEY = 'SECRET'

        with patch('boto3.client') as boto, \
             patch.object(unauthorized, 'authorize') as authorize:
            assert unauthorized._connect_client() is boto.return_value

        authorize.assert_not_called()
        assert boto.call_args[1]['aws_access_key_id'] == 'AKIA'
        assert boto.call_args[1]['region_name'] == 'us-east-1'

    @pytest.mark.unit
    def test_an_existing_client_is_reused(self, unauthorized):
        unauthorized.expiration = NOW_MS + HOUR_MS
        unauthorized.CLIENT = MagicMock()

        with patch('boto3.client') as boto:
            assert unauthorized._connect_client() is unauthorized.CLIENT

        boto.assert_not_called()

    @pytest.mark.unit
    def test_a_credential_inside_the_refresh_buffer_is_renewed(self, unauthorized):
        # Renewed early so an in-flight put_record cannot straddle expiry.
        unauthorized.expiration = NOW_MS + (REFRESH_BUFFER_MS - 1000)

        def authorize():
            unauthorized.ACCESS_KEY = 'NEW'
            unauthorized.SECRET_KEY = 'NEWSECRET'
            return True

        with patch('boto3.client') as boto, \
             patch.object(unauthorized, 'authorize', side_effect=authorize) as auth:
            unauthorized._connect_client()

        auth.assert_called_once()
        assert boto.call_args[1]['aws_access_key_id'] == 'NEW'

    @pytest.mark.unit
    def test_no_expiry_triggers_authorization(self, unauthorized):
        unauthorized.expiration = None

        with patch('boto3.client'), \
             patch.object(unauthorized, 'authorize', return_value=True) as auth:
            unauthorized._connect_client()

        auth.assert_called_once()

    @pytest.mark.unit
    def test_a_failed_authorization_yields_no_client(self, unauthorized):
        unauthorized.expiration = None

        with patch('boto3.client') as boto, \
             patch.object(unauthorized, 'authorize', return_value=False):
            assert unauthorized._connect_client() is False

        boto.assert_not_called()

    @pytest.mark.unit
    def test_a_recent_auth_failure_is_not_retried_immediately(self, unauthorized):
        # Without the backoff every record would re-attempt a failing auth.
        unauthorized.expiration = None
        unauthorized._last_auth_failure = NOW_MS - 1000

        with patch.object(unauthorized, 'authorize') as authorize:
            assert unauthorized._connect_client() is False

        authorize.assert_not_called()

    @pytest.mark.unit
    def test_the_backoff_expires(self, unauthorized):
        unauthorized.expiration = None
        unauthorized._last_auth_failure = NOW_MS - (unauthorized.AUTH_RETRY_MS + 1)

        with patch('boto3.client'), \
             patch.object(unauthorized, 'authorize', return_value=True) as authorize:
            unauthorized._connect_client()

        authorize.assert_called_once()

    @pytest.mark.unit
    def test_a_concurrent_refresh_is_not_repeated(self, unauthorized):
        # Two threads reaching the buffer together must produce one auth call,
        # not one per thread.
        unauthorized.expiration = None
        unauthorized.ACCESS_KEY = 'AKIA'

        real_lock = unauthorized._auth_lock

        class RefreshingLock:
            def __enter__(self):
                real_lock.acquire()
                unauthorized.expiration = NOW_MS + HOUR_MS
                return self

            def __exit__(self, *exc):
                real_lock.release()
                return False

        unauthorized._auth_lock = RefreshingLock()

        with patch('boto3.client'), \
             patch.object(unauthorized, 'authorize') as authorize:
            unauthorized._connect_client()

        authorize.assert_not_called()

    @pytest.mark.unit
    def test_no_credentials_at_all_yields_no_client(self, unauthorized):
        unauthorized.expiration = NOW_MS + HOUR_MS
        unauthorized.ACCESS_KEY = None

        assert unauthorized._connect_client() is False


class TestSendStream:
    @pytest.fixture
    def authorized(self, unauthorized):
        unauthorized.authorized = True
        unauthorized.stream = 'arn:stream'
        unauthorized.expiration = NOW_MS + HOUR_MS
        unauthorized.ACCESS_KEY = 'AKIA'
        unauthorized.SECRET_KEY = 'SECRET'
        return unauthorized

    @pytest.mark.unit
    def test_publishes_a_newline_delimited_record(self, authorized):
        client = MagicMock()
        with patch.object(authorized, '_connect_client', return_value=client):
            assert authorized.send_stream({'a': 1}, partition_key='pk') is True

        assert client.put_record.call_args[1]['StreamARN'] == 'arn:stream'
        assert client.put_record.call_args[1]['Data'] == '{"a": 1}\n'
        assert client.put_record.call_args[1]['PartitionKey'] == 'pk'

    @pytest.mark.unit
    def test_without_a_partition_key_records_are_spread_across_shards(self, authorized):
        client = MagicMock()
        with patch.object(authorized, '_connect_client', return_value=client):
            authorized.send_stream({'a': 1})
            authorized.send_stream({'a': 2})

        keys = [c[1]['PartitionKey'] for c in client.put_record.call_args_list]
        assert keys[0] != keys[1]

    @pytest.mark.unit
    def test_an_unauthorized_instance_refuses(self, unauthorized):
        unauthorized.authorized = False

        with patch.object(unauthorized, '_connect_client') as connect:
            assert unauthorized.send_stream({'a': 1}) == ('Service not authorized', 403)

        connect.assert_not_called()

    @pytest.mark.unit
    def test_no_client_returns_false(self, authorized):
        with patch.object(authorized, '_connect_client', return_value=False):
            assert authorized.send_stream({'a': 1}) is False

    @pytest.mark.unit
    @pytest.mark.parametrize('code', ['UnrecognizedClientException',
                                      'ExpiredTokenException',
                                      'InvalidSignatureException'])
    def test_an_auth_error_discards_the_stale_client(self, authorized, code):
        # Without this the instance would keep reusing a client AWS has already
        # rejected, and every later record would fail the same way.
        client = MagicMock()
        client.put_record.side_effect = botocore.exceptions.ClientError(
            {'Error': {'Code': code}}, 'PutRecord')

        with patch.object(authorized, '_connect_client', return_value=client), \
             patch.object(authorized, '_log') as log:
            with pytest.raises(botocore.exceptions.ClientError):
                authorized.send_stream({'a': 1})

        assert authorized.CLIENT is None
        assert authorized.expiration is None
        assert log.call_args[0][0] == 'put_record_auth_error'

    @pytest.mark.unit
    def test_a_non_auth_error_keeps_the_client(self, authorized):
        client = MagicMock()
        client.put_record.side_effect = botocore.exceptions.ClientError(
            {'Error': {'Code': 'ProvisionedThroughputExceededException'}}, 'PutRecord')
        authorized.CLIENT = client

        with patch.object(authorized, '_connect_client', return_value=client):
            with pytest.raises(botocore.exceptions.ClientError):
                authorized.send_stream({'a': 1})

        assert authorized.CLIENT is client
        assert authorized.expiration is not None


class TestLog:
    @pytest.mark.unit
    def test_writes_an_audit_record(self, unauthorized):
        with patch.object(kinesis_module, 'kinesis_log') as log:
            unauthorized._log('authorize_failed', {'status': 403})

        document = log.insert_one.call_args[0][0]
        assert document['event'] == 'authorize_failed'
        assert document['details'] == {'status': 403}
        assert document['ts_ms'] == NOW_MS

    @pytest.mark.unit
    def test_details_default_to_empty(self, unauthorized):
        with patch.object(kinesis_module, 'kinesis_log') as log:
            unauthorized._log('authorize_success')

        assert log.insert_one.call_args[0][0]['details'] == {}

    @pytest.mark.unit
    def test_a_mongo_failure_never_propagates(self, unauthorized):
        # Logging sits inside the publish path; failing here must not lose the
        # record or mask the original error.
        with patch.object(kinesis_module, 'kinesis_log') as log:
            log.insert_one.side_effect = Exception('no mongo')
            unauthorized._log('authorize_failed')


class TestGetAuthToken:
    @pytest.mark.unit
    def test_reads_the_stored_access_token(self, auth_token, unauthorized):
        assert unauthorized.get_auth_token() == 'id-token'
        auth_token.find_one.assert_called_once_with(
            {'type': 'access_token'}, {'_id': 0})

    @pytest.mark.unit
    def test_an_unregistered_device_raises(self, unauthorized):
        with patch.object(kinesis_module, 'util_ref') as util:
            util.find_one.return_value = None
            with pytest.raises(TypeError):
                unauthorized.get_auth_token()
