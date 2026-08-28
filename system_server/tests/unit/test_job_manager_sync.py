"""Analytics sync bookkeeping in job_manager.

The sync tracker is how a detection's delivery is proven: a record is only
considered done once it has been seen SYNC_COMPLETION_THRESHOLD times, and the
completion stamp must be written exactly once. The Kinesis path is the one that
has to survive partial failure - one bad record in a batch must not lose the
rest, and every failure must leave the record flagged for retry.

The existing test_job_manager.py covers the query and batching side; these
cover the tracker, the Kinesis fan-out and the feature installers.
"""
import pytest
from testsupport import thread_aware_sleep_mock
from unittest.mock import patch, MagicMock, call

from worker_scripts import job_manager as jm


NOW_MS = 1_700_000_000_000


@pytest.fixture
def tracker():
    collection = MagicMock()
    collection.find_one_and_update.return_value = {'count': 1, 'first_sync_ms': NOW_MS}
    with patch.object(jm, '_get_tracker_collection', return_value=collection):
        yield collection


@pytest.fixture
def frozen_clock():
    with patch.object(jm, 'time_now_ms', return_value=NOW_MS):
        yield


class TestGetTrackerCollection:
    @pytest.mark.unit
    def test_uses_the_dedicated_collection(self):
        with patch.object(jm, 'client') as client:
            jm._get_tracker_collection()

        client.__getitem__.return_value.__getitem__.assert_called_once_with(
            jm.TRACKER_COLLECTION_NAME)


class TestUpdateSyncTrackerSuccess:
    @pytest.mark.unit
    def test_a_successful_sync_increments_the_count(self, tracker, frozen_clock):
        jm.update_sync_tracker('did-1', success=True)

        query, update = tracker.find_one_and_update.call_args[0]
        assert query == {'_id': 'did-1', 'completed': {'$ne': True}}
        assert update['$inc'] == {'count': 1}
        assert update['$set']['last_sync_ms'] == NOW_MS

    @pytest.mark.unit
    def test_a_first_sync_seeds_the_record(self, tracker, frozen_clock):
        jm.update_sync_tracker('did-1', success=True)

        seed = tracker.find_one_and_update.call_args[0][1]['$setOnInsert']
        assert seed['completed'] is False
        assert seed['errors'] == []
        assert seed['first_sync_ms'] == NOW_MS
        assert tracker.find_one_and_update.call_args[1]['upsert'] is True

    @pytest.mark.unit
    def test_a_completed_record_is_not_touched_again(self, tracker, frozen_clock):
        # The query excludes completed:true, so a late duplicate delivery does
        # not restart the counter or move the completion stamp.
        jm.update_sync_tracker('did-1', success=True)

        assert tracker.find_one_and_update.call_args[0][0]['completed'] == {'$ne': True}

    @pytest.mark.unit
    def test_reaching_the_threshold_marks_it_complete(self, tracker, frozen_clock):
        tracker.find_one_and_update.return_value = {
            'count': jm.SYNC_COMPLETION_THRESHOLD,
            'first_sync_ms': NOW_MS - 5000,
            'completed': False,
        }

        jm.update_sync_tracker('did-1', success=True)

        query, update = tracker.update_one.call_args[0]
        assert query == {'_id': 'did-1', 'completed': {'$ne': True}}
        assert update['$set']['completed'] is True
        assert update['$set']['total_time_seconds'] == 5.0

    @pytest.mark.unit
    def test_below_the_threshold_is_not_marked_complete(self, tracker, frozen_clock):
        tracker.find_one_and_update.return_value = {
            'count': jm.SYNC_COMPLETION_THRESHOLD - 1,
            'first_sync_ms': NOW_MS,
        }

        jm.update_sync_tracker('did-1', success=True)

        tracker.update_one.assert_not_called()

    @pytest.mark.unit
    def test_an_already_completed_record_is_not_restamped(self, tracker, frozen_clock):
        tracker.find_one_and_update.return_value = {
            'count': jm.SYNC_COMPLETION_THRESHOLD + 5,
            'first_sync_ms': NOW_MS,
            'completed': True,
        }

        jm.update_sync_tracker('did-1', success=True)

        tracker.update_one.assert_not_called()

    @pytest.mark.unit
    def test_no_result_document_is_handled(self, tracker, frozen_clock):
        tracker.find_one_and_update.return_value = None

        jm.update_sync_tracker('did-1', success=True)

        tracker.update_one.assert_not_called()


class TestUpdateSyncTrackerFailure:
    @pytest.mark.unit
    def test_a_failure_appends_an_error_entry(self, tracker, frozen_clock):
        jm.update_sync_tracker('did-1', success=False,
                               error_msg='boom', record_id='rec-9')

        query, update = tracker.update_one.call_args[0]
        assert query == {'_id': 'did-1'}
        entry = update['$push']['errors']
        assert entry['error'] == 'boom'
        assert entry['record_id'] == 'rec-9'
        assert entry['timestamp_ms'] == NOW_MS

    @pytest.mark.unit
    def test_a_failure_does_not_increment_the_count(self, tracker, frozen_clock):
        jm.update_sync_tracker('did-1', success=False, error_msg='boom')

        update = tracker.update_one.call_args[0][1]
        assert '$inc' not in update
        assert update['$setOnInsert']['count'] == 0

    @pytest.mark.unit
    def test_a_first_failure_still_creates_the_record(self, tracker, frozen_clock):
        jm.update_sync_tracker('did-1', success=False, error_msg='boom')

        assert tracker.update_one.call_args[1]['upsert'] is True


class TestUpdateSyncTrackerResilience:
    @pytest.mark.unit
    def test_a_mongo_failure_never_propagates(self, frozen_clock):
        # The tracker is bookkeeping; failing here must not fail the sync that
        # already succeeded.
        with patch.object(jm, '_get_tracker_collection',
                          side_effect=Exception('no mongo')):
            jm.update_sync_tracker('did-1', success=True)


@pytest.fixture
def kinesis():
    # sync_tracker is a module-level flag that ships off; it is turned on here
    # so the tracking branches are exercised. TestSyncTrackerFlag below covers
    # the shipped default.
    with patch.object(jm, 'aws_client') as client, \
         patch.object(jm, 'analytics_coll') as analytics, \
         patch.object(jm, 'mark_as_synced') as synced, \
         patch.object(jm, 'update_sync_tracker') as tracker, \
         patch.object(jm, 'sync_tracker', True), \
         patch('time.sleep', new=thread_aware_sleep_mock()):
        client.send_stream.return_value = True
        yield {'client': client, 'analytics': analytics,
               'synced': synced, 'tracker': tracker}


def _record(record_id='rec-1', did='did-1'):
    return {'id': record_id, 'did': did, 'payload': {}}


class TestKinesisCall:
    @pytest.mark.unit
    def test_a_delivered_record_is_marked_synced(self, kinesis):
        assert jm.kinesis_call([_record()]) is True

        kinesis['client'].send_stream.assert_called_once()
        kinesis['synced'].assert_called_once_with('rec-1')
        kinesis['tracker'].assert_called_once_with(
            'did-1', success=True, record_id='rec-1')

    @pytest.mark.unit
    def test_every_record_in_a_batch_is_sent(self, kinesis):
        jm.kinesis_call([_record('r1'), _record('r2'), _record('r3')])

        assert kinesis['client'].send_stream.call_count == 3

    @pytest.mark.unit
    def test_the_mongo_id_is_stripped_before_sending(self, kinesis):
        record = dict(_record(), _id='oid')

        jm.kinesis_call([record])

        assert '_id' not in kinesis['client'].send_stream.call_args[0][0]

    @pytest.mark.unit
    def test_a_record_without_an_id_is_skipped_and_flagged(self, kinesis):
        assert jm.kinesis_call([{'did': 'did-1'}]) is False

        kinesis['client'].send_stream.assert_not_called()
        kinesis['tracker'].assert_called_once()
        assert kinesis['tracker'].call_args[1]['success'] is False

    @pytest.mark.unit
    def test_one_bad_record_does_not_stop_the_batch(self, kinesis):
        # A single malformed row must not strand the rest of the batch.
        assert jm.kinesis_call([{'did': 'x'}, _record('r2')]) is False

        kinesis['client'].send_stream.assert_called_once()
        kinesis['synced'].assert_called_once_with('r2')

    @pytest.mark.unit
    def test_a_rejected_send_flags_the_record_for_retry(self, kinesis):
        kinesis['client'].send_stream.return_value = False

        assert jm.kinesis_call([_record()]) is False

        kinesis['analytics'].update_one.assert_called_once_with(
            {'id': 'rec-1'}, {'$set': {'synced': False}})
        kinesis['synced'].assert_not_called()

    @pytest.mark.unit
    def test_a_raising_send_flags_the_record_for_retry(self, kinesis):
        kinesis['client'].send_stream.side_effect = RuntimeError('aws down')

        assert jm.kinesis_call([_record()]) is False

        kinesis['analytics'].update_one.assert_called_once_with(
            {'id': 'rec-1'}, {'$set': {'synced': False}})
        assert 'aws down' in kinesis['tracker'].call_args[1]['error_msg']

    @pytest.mark.unit
    def test_a_raising_send_does_not_stop_later_records(self, kinesis):
        kinesis['client'].send_stream.side_effect = [RuntimeError('aws down'), True]

        assert jm.kinesis_call([_record('r1'), _record('r2')]) is False

        kinesis['synced'].assert_called_once_with('r2')

    @pytest.mark.unit
    def test_tracking_is_off_in_the_shipped_configuration(self):
        # sync_tracker defaults to False, so none of the tracker writes happen
        # on a device unless it is flipped. Pinned so the shipped behaviour is
        # explicit rather than incidental.
        assert jm.sync_tracker is False

        with patch.object(jm, 'aws_client') as client, \
             patch.object(jm, 'analytics_coll'), \
             patch.object(jm, 'mark_as_synced'), \
             patch.object(jm, 'update_sync_tracker') as tracker, \
             patch('time.sleep', new=thread_aware_sleep_mock()):
            client.send_stream.return_value = True
            jm.kinesis_call([_record()])

        tracker.assert_not_called()

    @pytest.mark.unit
    def test_a_failure_outside_the_loop_flags_every_record(self, kinesis):
        # Nothing was proven delivered, so the whole batch has to be retried.
        with patch.object(jm, 'mark_as_synced', side_effect=RuntimeError('mongo')):
            assert jm.kinesis_call([_record('r1'), _record('r2')]) is False

        flagged = [c[0][0]['id'] for c in kinesis['analytics'].update_one.call_args_list]
        assert flagged == ['r1', 'r2']


class TestPushAnalyticsToCloud:
    @pytest.fixture
    def queue(self):
        with patch.object(jm, 'get_unsynced_records') as unsynced, \
             patch.object(jm.job_queue, 'enqueue',
                          return_value=MagicMock(id='job-1')) as enqueue, \
             patch.object(jm, 'insert_job') as insert:
            yield {'unsynced': unsynced, 'enqueue': enqueue, 'insert': insert}

    @pytest.mark.unit
    def test_nothing_to_sync_enqueues_nothing(self, queue):
        queue['unsynced'].return_value = []

        assert jm.push_analytics_to_cloud('https://cloud', 'tok') is True
        queue['enqueue'].assert_not_called()

    @pytest.mark.unit
    def test_records_are_enqueued_in_batches(self, queue):
        queue['unsynced'].return_value = [_record(f'r{i}') for i in range(25)]

        with patch.object(jm, 'use_aws', False):
            jm.push_analytics_to_cloud('https://cloud', 'tok')

        # 25 records at BATCH_SIZE 10 -> 3 jobs.
        assert queue['enqueue'].call_count == 3
        assert queue['insert'].call_count == 3

    @pytest.mark.unit
    def test_the_cloud_path_targets_the_upload_endpoint(self, queue):
        queue['unsynced'].return_value = [_record()]

        with patch.object(jm, 'use_aws', False):
            jm.push_analytics_to_cloud('https://cloud', 'tok')

        args = queue['enqueue'].call_args[0]
        assert args[0] is jm.cloud_call
        assert args[1] == 'https://cloud/api/capture/devices/upload_prediction'
        assert args[3]['Authorization'] == 'Bearer tok'

    @pytest.mark.unit
    def test_the_aws_path_targets_kinesis(self, queue):
        queue['unsynced'].return_value = [_record()]

        with patch.object(jm, 'use_aws', True):
            jm.push_analytics_to_cloud('https://cloud', 'tok')

        assert queue['enqueue'].call_args[0][0] is jm.kinesis_call

    @pytest.mark.unit
    def test_the_push_is_retried(self, queue):
        queue['unsynced'].return_value = [_record()]

        with patch.object(jm, 'use_aws', False):
            jm.push_analytics_to_cloud('https://cloud', 'tok')

        assert queue['enqueue'].call_args[1]['retry'].max == 5
        assert queue['enqueue'].call_args[1]['job_timeout'] == 300


class TestFeatureInstallers:
    """The shims kept for jobs queued before the addon migration.

    rq serialises a job by import path, so a job enqueued against the old
    function names has to keep resolving. Each now delegates to the addon it
    used to install by hand.
    """

    @pytest.mark.unit
    @pytest.mark.parametrize('installer,addon', [
        ('enable_ocr', 'ocr'),
        ('enable_assembly_guidance', 'assembly'),
        ('enable_audio', 'anomaly_audio'),
    ])
    def test_each_delegates_to_its_addon(self, installer, addon):
        with patch('addons.jobs.enable_addon') as enable:
            getattr(jm, installer)()

        enable.assert_called_once_with(addon)


@pytest.fixture
def cloud():
    with patch.object(jm, 'analytics_coll') as analytics, \
         patch.object(jm, 'mark_as_synced') as synced, \
         patch.object(jm, 'update_sync_tracker') as tracker, \
         patch.object(jm, 'sync_tracker', True), \
         patch('time.sleep', new=thread_aware_sleep_mock()):
        yield {'analytics': analytics, 'synced': synced, 'tracker': tracker}


class TestCloudCall:
    """The non-AWS upload path."""

    def _response(self, status=200, text=''):
        return MagicMock(status_code=status, text=text)

    @pytest.mark.unit
    def test_a_delivered_batch_is_marked_synced(self, cloud):
        with patch('requests.post', return_value=self._response()) as post:
            assert jm.cloud_call('https://cloud/upload',
                                 [_record('r1'), _record('r2')], {}) is True

        post.assert_called_once()
        assert [c[0][0] for c in cloud['synced'].call_args_list] == ['r1', 'r2']

    @pytest.mark.unit
    def test_each_delivered_record_is_tracked(self, cloud):
        with patch('requests.post', return_value=self._response()):
            jm.cloud_call('https://cloud/upload', [_record('r1')], {})

        cloud['tracker'].assert_called_once_with(
            'did-1', success=True, record_id='r1')

    @pytest.mark.unit
    def test_a_rejected_batch_is_flagged_for_retry(self, cloud):
        with patch('requests.post',
                   return_value=self._response(status=502, text='bad gateway')):
            assert jm.cloud_call('https://cloud/upload',
                                 [_record('r1'), _record('r2')], {}) is False

        flagged = [c[0][0]['id'] for c in cloud['analytics'].update_one.call_args_list]
        assert flagged == ['r1', 'r2']
        cloud['synced'].assert_not_called()

    @pytest.mark.unit
    def test_the_rejection_reason_is_recorded(self, cloud):
        with patch('requests.post',
                   return_value=self._response(status=502, text='bad gateway')):
            jm.cloud_call('https://cloud/upload', [_record('r1')], {})

        error = cloud['tracker'].call_args[1]['error_msg']
        assert 'HTTP 502' in error
        assert 'bad gateway' in error

    @pytest.mark.unit
    def test_a_long_error_body_is_truncated(self, cloud):
        # The tracker document grows one entry per failure; an unbounded body
        # would push it past the mongo document limit.
        with patch('requests.post',
                   return_value=self._response(status=500, text='x' * 1000)):
            jm.cloud_call('https://cloud/upload', [_record('r1')], {})

        assert len(cloud['tracker'].call_args[1]['error_msg']) < 250

    @pytest.mark.unit
    def test_an_unreachable_cloud_flags_the_whole_batch(self, cloud):
        with patch('requests.post', side_effect=ConnectionError('offline')):
            assert jm.cloud_call('https://cloud/upload',
                                 [_record('r1'), _record('r2')], {}) is False

        flagged = [c[0][0]['id'] for c in cloud['analytics'].update_one.call_args_list]
        assert flagged == ['r1', 'r2']
        assert 'offline' in cloud['tracker'].call_args[1]['error_msg']


class TestGetUnsyncedRecordsQuery:
    def _collection(self):
        collection = MagicMock()
        collection.find_one_and_update.return_value = None
        return collection

    @pytest.mark.unit
    def test_the_aws_path_only_takes_completed_detections(self):
        # Kinesis publishes one row per detection; sending a partial one would
        # publish a record that later changes.
        collection = self._collection()
        with patch.object(jm, 'find_utility', return_value=[{'ms_time': '0'}]), \
             patch.object(jm, 'analytics_coll', collection), \
             patch.object(jm, 'use_aws', True):
            jm.get_unsynced_records()

        assert collection.find_one_and_update.call_args_list[0][0][0]['complete'] is True

    @pytest.mark.unit
    def test_the_cloud_path_does_not_require_completion(self):
        collection = self._collection()
        with patch.object(jm, 'find_utility', return_value=[{'ms_time': '0'}]), \
             patch.object(jm, 'analytics_coll', collection), \
             patch.object(jm, 'use_aws', False):
            jm.get_unsynced_records()

        assert 'complete' not in collection.find_one_and_update.call_args_list[0][0][0]

    @pytest.mark.unit
    def test_records_are_claimed_before_being_returned(self):
        # Marking 'processing' on pickup is what stops two sync cycles from
        # uploading the same record twice.
        collection = self._collection()
        with patch.object(jm, 'find_utility', return_value=[{'ms_time': '0'}]), \
             patch.object(jm, 'analytics_coll', collection), \
             patch.object(jm, 'use_aws', False):
            jm.get_unsynced_records()

        update = collection.find_one_and_update.call_args_list[0][0][1]
        assert update['$set']['synced'] == 'processing'

    @pytest.mark.unit
    def test_recently_modified_records_are_left_alone(self):
        # A two-minute grace period so a record still being written is not
        # picked up mid-update.
        collection = self._collection()
        with patch.object(jm, 'find_utility', return_value=[{'ms_time': '0'}]), \
             patch.object(jm, 'analytics_coll', collection), \
             patch.object(jm, 'time_now_ms', return_value=NOW_MS), \
             patch.object(jm, 'use_aws', False):
            jm.get_unsynced_records()

        cutoff = collection.find_one_and_update.call_args_list[0][0][0]['modified']['$lt']
        assert cutoff == NOW_MS - 120000

    @pytest.mark.unit
    def test_a_device_with_no_sync_marker_seeds_one_and_returns_nothing(self):
        with patch.object(jm, 'find_utility', return_value=None), \
             patch.object(jm, 'util_collection') as utils, \
             patch.object(jm, 'analytics_coll') as analytics:
            assert jm.get_unsynced_records() == []

        assert utils.insert_one.call_args[0][0]['type'] == 'predict_sync'
        analytics.find_one_and_update.assert_not_called()
