"""
Unit tests for job_manager.py worker script
"""
import pytest
from testsupport import thread_aware_sleep_mock
import time
from unittest.mock import Mock, patch, MagicMock, call
from datetime import datetime


class TestInsertJob:
    """Tests for insert_job function"""

    @pytest.mark.unit
    @patch('worker_scripts.job_manager.job_collection')
    def test_insert_job_basic(self, mock_job_collection):
        """Test basic job insertion"""
        from worker_scripts.job_manager import insert_job

        job_id = 'test-job-123'
        msg = 'test_job_type'

        insert_job(job_id, msg)

        mock_job_collection.insert_one.assert_called_once()
        call_args = mock_job_collection.insert_one.call_args[0][0]

        assert call_args['_id'] == job_id
        assert call_args['type'] == msg
        assert call_args['status'] == 'running'
        assert 'start_time' in call_args

    @pytest.mark.unit
    @patch('worker_scripts.job_manager.job_collection')
    def test_insert_job_different_types(self, mock_job_collection):
        """Test job insertion with different message types"""
        from worker_scripts.job_manager import insert_job

        test_cases = [
            ('job1', 'sync_models'),
            ('job2', 'upload_data'),
            ('job3', 'process_image'),
        ]

        for job_id, msg in test_cases:
            insert_job(job_id, msg)

        assert mock_job_collection.insert_one.call_count == 3


class TestFindUtility:
    """Tests for find_utility function"""

    @pytest.mark.unit
    @patch('worker_scripts.job_manager.util_collection')
    def test_find_utility_basic(self, mock_util_collection):
        """Test finding a utility by type"""
        from worker_scripts.job_manager import find_utility

        mock_util_collection.find.return_value = [{'type': 'test_util', 'value': 'test'}]

        result = find_utility('test_util')

        mock_util_collection.find.assert_called_once_with(
            {'type': 'test_util'}, {'_id': 0}
        )
        assert isinstance(result, list)

    @pytest.mark.unit
    @patch('worker_scripts.job_manager.util_collection')
    def test_find_utility_not_found(self, mock_util_collection):
        """Test finding a utility that doesn't exist"""
        from worker_scripts.job_manager import find_utility

        mock_util_collection.find.return_value = []

        result = find_utility('nonexistent')

        mock_util_collection.find.assert_called_once()
        assert isinstance(result, list)


class TestTimeNowMs:
    """Tests for time_now_ms function"""

    @pytest.mark.unit
    @patch('time.time')
    def test_time_now_ms_returns_milliseconds(self, mock_time):
        """Test that time_now_ms returns time in milliseconds"""
        from worker_scripts.job_manager import time_now_ms

        mock_time.return_value = 1234567890.123456

        result = time_now_ms()

        expected = int(round(1234567890.123456 * 1000))
        assert result == expected
        assert isinstance(result, int)

    @pytest.mark.unit
    @patch('time.time')
    def test_time_now_ms_different_times(self, mock_time):
        """Test time_now_ms with different timestamps"""
        from worker_scripts.job_manager import time_now_ms

        test_times = [1000.5, 2000.75, 3000.999]
        expected_results = [1000500, 2000750, 3000999]

        for test_time, expected in zip(test_times, expected_results):
            mock_time.return_value = test_time
            result = time_now_ms()
            assert result == expected


class TestGetUnsyncedRecords:
    """Tests for get_unsynced_records function"""

    @pytest.mark.unit
    @patch('worker_scripts.job_manager.analytics_coll')
    @patch('worker_scripts.job_manager.util_collection')
    @patch('worker_scripts.job_manager.time_now_ms')
    @patch('time.sleep', new_callable=thread_aware_sleep_mock)
    def test_get_unsynced_records_claims_pending_then_stuck(
            self, mock_sleep, mock_time_now_ms, mock_util_collection,
            mock_analytics_coll):
        """Records are claimed one at a time with find_one_and_update.

        Deliberately does not assert on time.sleep. job_manager does
        `import time`, so patching its sleep patches the global one, which
        pymongo's monitor threads also call - the observed count ranged from
        28,000 to 55,000 across runs and made this test nondeterministic. The
        rate-limiting sleep is an implementation detail; the records returned
        are the contract.
        """
        from worker_scripts.job_manager import get_unsynced_records

        mock_time_now_ms.return_value = 1000000
        mock_util_collection.find.return_value = [{'type': 'predict_sync'}]

        pending = {'id': 'rec1', 'synced': False, 'modified': 950000}
        stuck = {'id': 'rec2', 'synced': 'processing', 'modified': 950000}
        # Pending loop claims rec1 then drains; stuck loop then claims rec2.
        mock_analytics_coll.find_one_and_update.side_effect = [pending, None, stuck, None]

        result = get_unsynced_records()

        assert [r['id'] for r in result] == ['rec1', 'rec2']
        # Stops at the first empty result rather than burning the whole
        # 1000-iteration budget on a quiet device.
        assert mock_analytics_coll.find_one_and_update.call_count == 4

    @pytest.mark.unit
    @patch('worker_scripts.job_manager.analytics_coll')
    @patch('worker_scripts.job_manager.util_collection')
    @patch('worker_scripts.job_manager.time_now_ms')
    def test_get_unsynced_records_creates_sync_obj(self, mock_time_now_ms,
                                                     mock_util_collection, mock_analytics_coll):
        """Test that sync object is created if it doesn't exist"""
        from worker_scripts.job_manager import get_unsynced_records

        mock_time_now_ms.return_value = 1000000
        mock_util_collection.find.return_value = []

        get_unsynced_records()

        mock_util_collection.insert_one.assert_called_once()
        call_args = mock_util_collection.insert_one.call_args[0][0]
        assert call_args['type'] == 'predict_sync'
        assert 'ms_time' in call_args


class TestMarkAsProcessing:
    """Tests for mark_as_processing function"""

    @pytest.mark.unit
    @patch('worker_scripts.job_manager.analytics_coll')
    @patch('worker_scripts.job_manager.time_now_ms')
    def test_mark_as_processing(self, mock_time_now_ms, mock_analytics_coll):
        """Test marking a record as processing"""
        from worker_scripts.job_manager import mark_as_processing

        mock_time_now_ms.return_value = 1234567890
        record_id = 'test-record-123'

        mark_as_processing(record_id)

        mock_analytics_coll.update_one.assert_called_once_with(
            {"id": record_id},
            {"$set": {"synced": "processing", "modified": 1234567890}},
            True
        )


class TestMarkAsSynced:
    """Tests for mark_as_synced function"""

    @pytest.mark.unit
    @patch('worker_scripts.job_manager.analytics_coll')
    def test_mark_as_synced(self, mock_analytics_coll):
        """Test marking a record as synced (removes it)"""
        from worker_scripts.job_manager import mark_as_synced

        record_id = 'test-record-456'

        mark_as_synced(record_id)

        mock_analytics_coll.delete_one.assert_called_once_with({"id": record_id})


class TestCloudCall:
    """Tests for cloud_call function"""

    @pytest.mark.unit
    @patch('worker_scripts.job_manager.mark_as_synced')
    @patch('requests.post')
    @patch('time.sleep', new_callable=thread_aware_sleep_mock)
    def test_cloud_call_success(self, mock_sleep, mock_post, mock_mark_synced):
        """Test successful cloud API call"""
        from worker_scripts.job_manager import cloud_call

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        url = 'http://test.com/api'
        analytics = [{'id': 'rec1'}, {'id': 'rec2'}]
        headers = {'Authorization': 'Bearer token'}

        result = cloud_call(url, analytics, headers)

        assert result is True
        # cloud_call posts to EITHER the passed url (environ == 'local') or
        # BQ_INGEST_PATH - an if/else, so exactly one request per batch.
        assert mock_post.call_count == 1
        assert mock_mark_synced.call_count == 2
        mock_sleep.assert_called_once_with(1)

    @pytest.mark.unit
    @patch('worker_scripts.job_manager.mark_as_synced')
    @patch('requests.post')
    @patch('time.sleep', new_callable=thread_aware_sleep_mock)
    def test_cloud_call_targets_bq_when_not_local(self, mock_sleep, mock_post,
                                                  mock_mark_synced):
        """A cloud device sends analytics to the ingest queue, not the domain."""
        from worker_scripts.job_manager import cloud_call, BQ_INGEST_PATH

        mock_post.return_value = MagicMock(status_code=200)

        with patch.dict('worker_scripts.job_manager.config', {'environ': 'cloud'}):
            cloud_call('http://test.com/api', [{'id': 'rec1'}], {})

        assert mock_post.call_args[0][0] == BQ_INGEST_PATH

    @pytest.mark.unit
    @patch('worker_scripts.job_manager.mark_as_synced')
    @patch('requests.post')
    @patch('time.sleep', new_callable=thread_aware_sleep_mock)
    def test_cloud_call_targets_the_url_when_local(self, mock_sleep, mock_post,
                                                   mock_mark_synced):
        """A local-cluster device posts to the master it was given."""
        from worker_scripts.job_manager import cloud_call

        mock_post.return_value = MagicMock(status_code=200)

        with patch.dict('worker_scripts.job_manager.config', {'environ': 'local'}):
            cloud_call('http://master.local/api', [{'id': 'rec1'}], {})

        assert mock_post.call_args[0][0] == 'http://master.local/api'

    @pytest.mark.unit
    @patch('requests.post')
    def test_cloud_call_empty_analytics(self, mock_post):
        """Test cloud call with empty analytics returns True"""
        from worker_scripts.job_manager import cloud_call

        result = cloud_call('http://test.com', [], {})

        assert result is True
        mock_post.assert_not_called()

    @pytest.mark.unit
    @patch('requests.post')
    @patch('time.sleep', new_callable=thread_aware_sleep_mock)
    def test_cloud_call_failure(self, mock_sleep, mock_post):
        """Test cloud call handles request failure"""
        from worker_scripts.job_manager import cloud_call

        mock_post.side_effect = Exception('Network error')

        url = 'http://test.com/api'
        analytics = [{'id': 'rec1'}]
        headers = {'Authorization': 'Bearer token'}

        result = cloud_call(url, analytics, headers)

        assert result is False

    @pytest.mark.unit
    @patch('worker_scripts.job_manager.mark_as_synced')
    @patch('requests.post')
    @patch('time.sleep', new_callable=thread_aware_sleep_mock)
    def test_cloud_call_non_200_status(self, mock_sleep, mock_post, mock_mark_synced):
        """Test cloud call with non-200 status code"""
        from worker_scripts.job_manager import cloud_call

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_post.return_value = mock_response

        url = 'http://test.com/api'
        analytics = [{'id': 'rec1'}]
        headers = {'Authorization': 'Bearer token'}

        result = cloud_call(url, analytics, headers)

        assert result is False
        mock_mark_synced.assert_not_called()


class TestKinesisCall:
    """Tests for kinesis_call function (AWS integration)"""

    @pytest.mark.unit
    @patch('worker_scripts.job_manager.mark_as_synced')
    @patch('worker_scripts.job_manager.aws_client')
    @patch('time.sleep', new_callable=thread_aware_sleep_mock)
    def test_kinesis_call_success(self, mock_sleep, mock_aws_client, mock_mark_synced):
        """Test successful Kinesis stream call"""
        from worker_scripts.job_manager import kinesis_call

        mock_aws_client.send_stream.return_value = True

        analytics = [
            {'id': 'rec1', '_id': 'mongo_id_1'},
            {'id': 'rec2', '_id': 'mongo_id_2'}
        ]

        result = kinesis_call(analytics)

        assert result is True
        assert mock_aws_client.send_stream.call_count == 2
        assert mock_mark_synced.call_count == 2
        mock_sleep.assert_called_once_with(1)

    @pytest.mark.unit
    @patch('worker_scripts.job_manager.aws_client')
    def test_kinesis_call_removes_id_field(self, mock_aws_client):
        """Test that _id field is removed before sending to Kinesis"""
        from worker_scripts.job_manager import kinesis_call

        mock_aws_client.send_stream.return_value = True

        analytics = [{'id': 'rec1', '_id': 'should_be_removed', 'data': 'test'}]

        kinesis_call(analytics)

        # Check that the analytics object no longer has _id
        assert '_id' not in analytics[0]

    @pytest.mark.unit
    def test_kinesis_call_empty_analytics(self):
        """Test Kinesis call with empty analytics"""
        from worker_scripts.job_manager import kinesis_call

        result = kinesis_call([])

        assert result is True

    @pytest.mark.unit
    @patch('worker_scripts.job_manager.aws_client')
    def test_kinesis_call_failure(self, mock_aws_client):
        """Test Kinesis call handles errors"""
        from worker_scripts.job_manager import kinesis_call

        mock_aws_client.send_stream.side_effect = Exception('Kinesis error')

        analytics = [{'id': 'rec1'}]

        result = kinesis_call(analytics)

        assert result is False


# TestPushAnalyticsToCloudBatch was removed: it tested
# push_analytics_to_cloud_batch(), which was never implemented in
# job_manager.py (checked the full history). The tests could only ever
# fail on ImportError, and one of them wrapped its assertions in
# `except NameError: pass` so it asserted nothing. The function that does
# exist, push_analytics_to_cloud(), is covered by the class below.

class TestPushAnalyticsToCloud:
    """Tests for push_analytics_to_cloud function"""

    @pytest.mark.unit
    @patch('worker_scripts.job_manager.use_aws', False)
    @patch('worker_scripts.job_manager.job_queue')
    @patch('worker_scripts.job_manager.get_unsynced_records')
    @patch('worker_scripts.job_manager.insert_job')
    def test_push_analytics_to_cloud_basic(self, mock_insert_job, mock_get_unsynced,
                                            mock_job_queue):
        """Test basic analytics push to cloud"""
        from worker_scripts.job_manager import push_analytics_to_cloud

        mock_job = MagicMock()
        mock_job.id = 'job-456'
        mock_job_queue.enqueue.return_value = mock_job

        analytics = [{'id': f'rec{i}'} for i in range(5)]
        mock_get_unsynced.return_value = analytics

        result = push_analytics_to_cloud('http://test.com', 'token123')

        assert result is True
        mock_job_queue.enqueue.assert_called_once()

    @pytest.mark.unit
    @patch('worker_scripts.job_manager.use_aws', True)
    @patch('worker_scripts.job_manager.job_queue')
    @patch('worker_scripts.job_manager.get_unsynced_records')
    @patch('worker_scripts.job_manager.insert_job')
    def test_push_analytics_to_cloud_aws(self, mock_insert_job, mock_get_unsynced,
                                          mock_job_queue):
        """Test analytics push using AWS Kinesis"""
        from worker_scripts.job_manager import push_analytics_to_cloud

        mock_job = MagicMock()
        mock_job.id = 'job-789'
        mock_job_queue.enqueue.return_value = mock_job

        analytics = [{'id': f'rec{i}'} for i in range(5)]
        mock_get_unsynced.return_value = analytics

        result = push_analytics_to_cloud('http://test.com', 'token123')

        assert result is True
        # Verify kinesis_call is queued instead of cloud_call
        call_args = mock_job_queue.enqueue.call_args
        assert 'kinesis_call' in str(call_args) or call_args[0][0].__name__ == 'kinesis_call'


class TestEnableOCR:
    """Tests for the enable_ocr shim"""

    @pytest.mark.unit
    def test_enable_ocr(self):
        """OCR now deploys through the addon registry, not an install script"""
        from worker_scripts.job_manager import enable_ocr

        with patch('addons.jobs.enable_addon') as enable:
            enable_ocr()

        enable.assert_called_once_with('ocr')


class TestBatchSize:
    """Tests for batch size constant"""

    @pytest.mark.unit
    def test_batch_size_constant(self):
        """Test that BATCH_SIZE is set correctly"""
        from worker_scripts.job_manager import BATCH_SIZE

        assert BATCH_SIZE == 10
        assert isinstance(BATCH_SIZE, int)


class TestBQIngestPath:
    """Tests for BigQuery ingest path configuration"""

    @pytest.mark.unit
    @patch('worker_scripts.job_manager.config', {'latest_stable_ref': 'latest_stable_version'})
    def test_bq_ingest_path_prod(self):
        """Test BQ ingest path for production"""
        # This test verifies the module-level logic
        # In actual implementation, this is set at import time
        pass

    @pytest.mark.unit
    @patch('worker_scripts.job_manager.config', {'latest_stable_ref': 'dev_version'})
    def test_bq_ingest_path_dev(self):
        """Test BQ ingest path for development"""
        # This test verifies the module-level logic
        pass
