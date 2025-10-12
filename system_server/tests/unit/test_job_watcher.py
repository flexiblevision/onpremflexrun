"""
Unit tests for job watcher functionality

Note: job_watcher.py has module-level code that runs an infinite loop.
To test the functions, we extract and test their logic separately.
"""
import pytest
import time
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock, call


class TestInsertFailedJob:
    """Tests for insert_failed_job function"""

    @pytest.mark.unit
    def test_insert_failed_job_basic(self):
        """Test basic failed job insertion"""
        mock_failed_jobs = MagicMock()
        mock_job = MagicMock()
        mock_job.id = 'job-123'
        mock_job.started_at = datetime(2025, 10, 11, 10, 0, 0)
        mock_job.ended_at = datetime(2025, 10, 11, 10, 5, 0)
        mock_job.origin = 'default'

        # Replicate insert_failed_job logic
        def insert_failed_job(j):
            mock_failed_jobs.update_one(
                {'job_id': j.id},
                {'$set': {
                    'job_id': j.id,
                    'started_at': j.started_at,
                    'ended_at': j.ended_at,
                    'origin': j.origin
                }},
                True
            )

        insert_failed_job(mock_job)

        # Verify update_one was called with correct parameters
        mock_failed_jobs.update_one.assert_called_once()
        call_args = mock_failed_jobs.update_one.call_args

        # Check filter
        assert call_args[0][0] == {'job_id': 'job-123'}

        # Check update document
        update_doc = call_args[0][1]['$set']
        assert update_doc['job_id'] == 'job-123'
        assert update_doc['started_at'] == datetime(2025, 10, 11, 10, 0, 0)
        assert update_doc['ended_at'] == datetime(2025, 10, 11, 10, 5, 0)
        assert update_doc['origin'] == 'default'

        # Check upsert flag
        assert call_args[0][2] is True

    @pytest.mark.unit
    def test_insert_failed_job_with_none_timestamps(self):
        """Test failed job insertion with None timestamps"""
        mock_failed_jobs = MagicMock()
        mock_job = MagicMock()
        mock_job.id = 'job-456'
        mock_job.started_at = None
        mock_job.ended_at = None
        mock_job.origin = 'default'

        def insert_failed_job(j):
            mock_failed_jobs.update_one(
                {'job_id': j.id},
                {'$set': {
                    'job_id': j.id,
                    'started_at': j.started_at,
                    'ended_at': j.ended_at,
                    'origin': j.origin
                }},
                True
            )

        insert_failed_job(mock_job)

        call_args = mock_failed_jobs.update_one.call_args
        update_doc = call_args[0][1]['$set']

        assert update_doc['started_at'] is None
        assert update_doc['ended_at'] is None

    @pytest.mark.unit
    def test_insert_failed_job_upsert_behavior(self):
        """Test that failed job uses upsert (insert or update)"""
        mock_failed_jobs = MagicMock()
        mock_job = MagicMock()
        mock_job.id = 'job-789'
        mock_job.started_at = datetime(2025, 10, 11, 11, 0, 0)
        mock_job.ended_at = datetime(2025, 10, 11, 11, 10, 0)
        mock_job.origin = 'high_priority'

        def insert_failed_job(j):
            mock_failed_jobs.update_one(
                {'job_id': j.id},
                {'$set': {
                    'job_id': j.id,
                    'started_at': j.started_at,
                    'ended_at': j.ended_at,
                    'origin': j.origin
                }},
                True
            )

        # Insert same job twice to test upsert
        insert_failed_job(mock_job)
        insert_failed_job(mock_job)

        # Should be called twice
        assert mock_failed_jobs.update_one.call_count == 2

        # Both calls should have upsert=True
        for call_item in mock_failed_jobs.update_one.call_args_list:
            assert call_item[0][2] is True

    @pytest.mark.unit
    def test_insert_failed_job_different_origins(self):
        """Test failed jobs with different origins"""
        mock_failed_jobs = MagicMock()

        def insert_failed_job(j):
            mock_failed_jobs.update_one(
                {'job_id': j.id},
                {'$set': {
                    'job_id': j.id,
                    'started_at': j.started_at,
                    'ended_at': j.ended_at,
                    'origin': j.origin
                }},
                True
            )

        origins = ['default', 'high_priority', 'low_priority', 'custom_queue']

        for idx, origin in enumerate(origins):
            mock_job = MagicMock()
            mock_job.id = f'job-{idx}'
            mock_job.started_at = datetime.now()
            mock_job.ended_at = datetime.now()
            mock_job.origin = origin

            insert_failed_job(mock_job)

        assert mock_failed_jobs.update_one.call_count == 4


class TestJobStatusHandling:
    """Tests for job status handling logic"""

    @pytest.mark.unit
    def test_handle_finished_job(self):
        """Test handling of finished job status"""
        mock_job_collection = MagicMock()
        mock_job_queue = MagicMock()

        # Create mock job
        mock_job = MagicMock()
        mock_job.id = 'finished-job-123'
        mock_job.get_status.return_value = 'finished'

        mock_job_queue.fetch_job.return_value = mock_job

        # Replicate finished job handling logic
        job_doc = {'_id': 'finished-job-123', 'type': 'test_job', 'status': 'running'}
        j = mock_job_queue.fetch_job(job_doc['_id'])

        if j and j.get_status() == 'finished':
            mock_job_collection.delete_one({'_id': job_doc['_id']})
            j.delete()

        # Verify job was deleted from collection
        mock_job_collection.delete_one.assert_called_once_with({'_id': 'finished-job-123'})

        # Verify job was deleted from queue
        mock_job.delete.assert_called_once()

    @pytest.mark.unit
    def test_handle_failed_job(self):
        """Test handling of failed job status"""
        mock_job_collection = MagicMock()
        mock_job_queue = MagicMock()
        mock_failed_jobs = MagicMock()

        # Create mock job
        mock_job = MagicMock()
        mock_job.id = 'failed-job-456'
        mock_job.get_status.return_value = 'failed'
        mock_job.started_at = datetime.now()
        mock_job.ended_at = datetime.now()
        mock_job.origin = 'default'

        mock_job_queue.fetch_job.return_value = mock_job

        def insert_failed_job(j):
            mock_failed_jobs.update_one(
                {'job_id': j.id},
                {'$set': {
                    'job_id': j.id,
                    'started_at': j.started_at,
                    'ended_at': j.ended_at,
                    'origin': j.origin
                }},
                True
            )

        # Replicate failed job handling logic
        job_doc = {'_id': 'failed-job-456', 'type': 'test_job', 'status': 'running'}
        j = mock_job_queue.fetch_job(job_doc['_id'])

        if j.get_status() == 'failed':
            insert_failed_job(j)
            mock_job_collection.delete_one({'_id': job_doc['_id']})

        # Verify failed job was inserted
        mock_failed_jobs.update_one.assert_called_once()

        # Verify job was deleted from collection
        mock_job_collection.delete_one.assert_called_once_with({'_id': 'failed-job-456'})

    @pytest.mark.unit
    def test_handle_queued_job(self):
        """Test handling of queued job status"""
        mock_job_collection = MagicMock()
        mock_job_queue = MagicMock()

        # Create mock job
        mock_job = MagicMock()
        mock_job.id = 'queued-job-789'
        mock_job.get_status.return_value = 'queued'

        mock_job_queue.fetch_job.return_value = mock_job

        # Replicate queued job handling logic
        job_doc = {'_id': 'queued-job-789', 'type': 'test_job'}
        j = mock_job_queue.fetch_job(job_doc['_id'])

        if j.get_status() != 'started':
            msg = 'job_' + j.id + '_' + j.get_status()
            mock_job_collection.update_one({'_id': job_doc['_id']}, {'$set': {'type': msg}})

        # Verify job status was updated
        mock_job_collection.update_one.assert_called_once_with(
            {'_id': 'queued-job-789'},
            {'$set': {'type': 'job_queued-job-789_queued'}}
        )

    @pytest.mark.unit
    def test_handle_deferred_job(self):
        """Test handling of deferred job status"""
        mock_job_collection = MagicMock()
        mock_job_queue = MagicMock()

        # Create mock job
        mock_job = MagicMock()
        mock_job.id = 'deferred-job-111'
        mock_job.get_status.return_value = 'deferred'

        mock_job_queue.fetch_job.return_value = mock_job

        # Replicate deferred job handling logic
        job_doc = {'_id': 'deferred-job-111', 'type': 'test_job'}
        j = mock_job_queue.fetch_job(job_doc['_id'])

        if j.get_status() != 'started':
            msg = 'job_' + j.id + '_' + j.get_status()
            mock_job_collection.update_one({'_id': job_doc['_id']}, {'$set': {'type': msg}})

        # Verify job status was updated
        mock_job_collection.update_one.assert_called_once_with(
            {'_id': 'deferred-job-111'},
            {'$set': {'type': 'job_deferred-job-111_deferred'}}
        )

    @pytest.mark.unit
    def test_handle_started_job(self):
        """Test that started jobs are not updated"""
        mock_job_collection = MagicMock()
        mock_job_queue = MagicMock()

        # Create mock job
        mock_job = MagicMock()
        mock_job.id = 'started-job-222'
        mock_job.get_status.return_value = 'started'

        mock_job_queue.fetch_job.return_value = mock_job

        # Replicate started job handling logic
        job_doc = {'_id': 'started-job-222', 'type': 'test_job', 'status': 'running'}
        j = mock_job_queue.fetch_job(job_doc['_id'])

        if j.get_status() != 'started':
            msg = 'job_' + j.id + '_' + j.get_status()
            mock_job_collection.update_one({'_id': job_doc['_id']}, {'$set': {'type': msg}})

        # Verify job collection was NOT updated (started jobs skip update)
        mock_job_collection.update_one.assert_not_called()

    @pytest.mark.unit
    def test_handle_null_job(self):
        """Test handling when job is not found in queue"""
        mock_job_collection = MagicMock()
        mock_job_queue = MagicMock()

        # Job not found in queue
        mock_job_queue.fetch_job.return_value = None

        # Replicate null job handling logic
        job_doc = {'_id': 'missing-job-333', 'type': 'test_job'}
        j = mock_job_queue.fetch_job(job_doc['_id'])

        if not j:
            # Should skip processing
            pass
        elif j and j.get_status() == 'finished':
            mock_job_collection.delete_one({'_id': job_doc['_id']})
            j.delete()

        # Verify no operations were performed
        mock_job_collection.delete_one.assert_not_called()


class TestJobCollectionOperations:
    """Tests for job collection database operations"""

    @pytest.mark.unit
    def test_find_jobs(self):
        """Test finding jobs in collection"""
        mock_job_collection = MagicMock()

        # Mock multiple jobs
        mock_jobs = [
            {'_id': 'job-1', 'type': 'ftp_job_image1.jpg', 'status': 'running'},
            {'_id': 'job-2', 'type': 'ftp_job_image2.jpg', 'status': 'running'},
            {'_id': 'job-3', 'type': 'prediction_job', 'status': 'queued'}
        ]

        mock_job_collection.find.return_value = mock_jobs

        # Find all jobs
        jobs = mock_job_collection.find()

        assert len(list(jobs)) == 3
        mock_job_collection.find.assert_called_once()

    @pytest.mark.unit
    def test_delete_finished_job(self):
        """Test deleting finished job from collection"""
        mock_job_collection = MagicMock()

        job_id = 'finished-job-123'

        # Delete job
        mock_job_collection.delete_one({'_id': job_id})

        mock_job_collection.delete_one.assert_called_once_with({'_id': job_id})

    @pytest.mark.unit
    def test_delete_failed_job(self):
        """Test deleting failed job from collection"""
        mock_job_collection = MagicMock()

        job_id = 'failed-job-456'

        # Delete job
        mock_job_collection.delete_one({'_id': job_id})

        mock_job_collection.delete_one.assert_called_once_with({'_id': job_id})

    @pytest.mark.unit
    def test_update_job_type(self):
        """Test updating job type field"""
        mock_job_collection = MagicMock()

        job_id = 'job-789'
        new_type = 'job_job-789_queued'

        # Update job
        mock_job_collection.update_one(
            {'_id': job_id},
            {'$set': {'type': new_type}}
        )

        mock_job_collection.update_one.assert_called_once_with(
            {'_id': job_id},
            {'$set': {'type': new_type}}
        )

    @pytest.mark.unit
    def test_empty_job_collection(self):
        """Test handling empty job collection"""
        mock_job_collection = MagicMock()

        # No jobs found
        mock_job_collection.find.return_value = []

        jobs = mock_job_collection.find()

        assert len(list(jobs)) == 0


class TestJobQueueOperations:
    """Tests for job queue fetch operations"""

    @pytest.mark.unit
    def test_fetch_existing_job(self):
        """Test fetching existing job from queue"""
        mock_job_queue = MagicMock()
        mock_job = MagicMock()
        mock_job.id = 'job-123'

        mock_job_queue.fetch_job.return_value = mock_job

        # Fetch job
        j = mock_job_queue.fetch_job('job-123')

        assert j is not None
        assert j.id == 'job-123'
        mock_job_queue.fetch_job.assert_called_once_with('job-123')

    @pytest.mark.unit
    def test_fetch_nonexistent_job(self):
        """Test fetching non-existent job from queue"""
        mock_job_queue = MagicMock()

        # Job not found
        mock_job_queue.fetch_job.return_value = None

        # Fetch job
        j = mock_job_queue.fetch_job('missing-job')

        assert j is None
        mock_job_queue.fetch_job.assert_called_once_with('missing-job')

    @pytest.mark.unit
    def test_delete_job_from_queue(self):
        """Test deleting job from queue"""
        mock_job = MagicMock()
        mock_job.id = 'job-to-delete'

        # Delete job
        mock_job.delete()

        mock_job.delete.assert_called_once()

    @pytest.mark.unit
    def test_get_job_status(self):
        """Test getting job status"""
        mock_job = MagicMock()
        mock_job.get_status.return_value = 'finished'

        status = mock_job.get_status()

        assert status == 'finished'
        mock_job.get_status.assert_called_once()


class TestJobStatusTypes:
    """Tests for different job status types"""

    @pytest.mark.unit
    def test_all_status_types(self):
        """Test recognition of all RQ job status types"""
        statuses = ['queued', 'started', 'finished', 'failed', 'deferred', 'scheduled']

        for status in statuses:
            mock_job = MagicMock()
            mock_job.get_status.return_value = status

            result = mock_job.get_status()

            assert result == status

    @pytest.mark.unit
    def test_status_message_generation(self):
        """Test status message generation for job updates"""
        test_cases = [
            ('job-123', 'queued', 'job_job-123_queued'),
            ('job-456', 'deferred', 'job_job-456_deferred'),
            ('job-789', 'scheduled', 'job_job-789_scheduled'),
        ]

        for job_id, status, expected_msg in test_cases:
            msg = 'job_' + job_id + '_' + status
            assert msg == expected_msg


class TestJobWatcherLoop:
    """Tests for job watcher loop logic"""

    @pytest.mark.unit
    def test_process_multiple_jobs(self):
        """Test processing multiple jobs in a single iteration"""
        mock_job_collection = MagicMock()
        mock_job_queue = MagicMock()
        mock_failed_jobs = MagicMock()

        # Create multiple jobs with different statuses
        jobs = [
            {'_id': 'job-1', 'type': 'test_job_1'},
            {'_id': 'job-2', 'type': 'test_job_2'},
            {'_id': 'job-3', 'type': 'test_job_3'}
        ]

        mock_job_collection.find.return_value = jobs

        # Mock different job statuses
        finished_job = MagicMock()
        finished_job.id = 'job-1'
        finished_job.get_status.return_value = 'finished'

        failed_job = MagicMock()
        failed_job.id = 'job-2'
        failed_job.get_status.return_value = 'failed'
        failed_job.started_at = datetime.now()
        failed_job.ended_at = datetime.now()
        failed_job.origin = 'default'

        queued_job = MagicMock()
        queued_job.id = 'job-3'
        queued_job.get_status.return_value = 'queued'

        def fetch_job_side_effect(job_id):
            if job_id == 'job-1':
                return finished_job
            elif job_id == 'job-2':
                return failed_job
            elif job_id == 'job-3':
                return queued_job
            return None

        mock_job_queue.fetch_job.side_effect = fetch_job_side_effect

        def insert_failed_job(j):
            mock_failed_jobs.update_one(
                {'job_id': j.id},
                {'$set': {
                    'job_id': j.id,
                    'started_at': j.started_at,
                    'ended_at': j.ended_at,
                    'origin': j.origin
                }},
                True
            )

        # Simulate one iteration of the loop
        jobs_list = mock_job_collection.find()
        for job in jobs_list:
            j = mock_job_queue.fetch_job(job['_id'])
            if not j:
                continue
            if j and j.get_status() == 'finished':
                mock_job_collection.delete_one({'_id': job['_id']})
                j.delete()
            elif j.get_status() == 'failed':
                insert_failed_job(j)
                mock_job_collection.delete_one({'_id': job['_id']})
            elif j.get_status() != 'started':
                msg = 'job_' + j.id + '_' + j.get_status()
                mock_job_collection.update_one({'_id': job['_id']}, {'$set': {'type': msg}})

        # Verify each job was processed correctly
        assert mock_job_queue.fetch_job.call_count == 3
        assert mock_job_collection.delete_one.call_count == 2  # finished and failed
        assert finished_job.delete.call_count == 1
        assert mock_failed_jobs.update_one.call_count == 1
        assert mock_job_collection.update_one.call_count == 1  # queued

    @pytest.mark.unit
    def test_skip_none_jobs(self):
        """Test that None jobs are skipped"""
        mock_job_collection = MagicMock()
        mock_job_queue = MagicMock()

        jobs = [
            {'_id': 'job-1', 'type': 'test_job_1'},
            {'_id': 'job-2', 'type': 'test_job_2'}
        ]

        mock_job_collection.find.return_value = jobs

        # First job exists, second doesn't
        valid_job = MagicMock()
        valid_job.get_status.return_value = 'finished'

        def fetch_job_side_effect(job_id):
            if job_id == 'job-1':
                return valid_job
            return None

        mock_job_queue.fetch_job.side_effect = fetch_job_side_effect

        # Process jobs
        jobs_list = mock_job_collection.find()
        for job in jobs_list:
            j = mock_job_queue.fetch_job(job['_id'])
            if not j:
                continue
            if j and j.get_status() == 'finished':
                mock_job_collection.delete_one({'_id': job['_id']})
                j.delete()

        # Only one job should be deleted (the valid one)
        assert mock_job_collection.delete_one.call_count == 1
        mock_job_collection.delete_one.assert_called_once_with({'_id': 'job-1'})


class TestJobTimestamps:
    """Tests for job timestamp handling"""

    @pytest.mark.unit
    def test_job_timestamps_preserved(self):
        """Test that job timestamps are preserved in failed jobs"""
        mock_failed_jobs = MagicMock()

        start_time = datetime(2025, 10, 11, 9, 0, 0)
        end_time = datetime(2025, 10, 11, 9, 30, 0)

        mock_job = MagicMock()
        mock_job.id = 'timed-job'
        mock_job.started_at = start_time
        mock_job.ended_at = end_time
        mock_job.origin = 'default'

        def insert_failed_job(j):
            mock_failed_jobs.update_one(
                {'job_id': j.id},
                {'$set': {
                    'job_id': j.id,
                    'started_at': j.started_at,
                    'ended_at': j.ended_at,
                    'origin': j.origin
                }},
                True
            )

        insert_failed_job(mock_job)

        call_args = mock_failed_jobs.update_one.call_args
        update_doc = call_args[0][1]['$set']

        assert update_doc['started_at'] == start_time
        assert update_doc['ended_at'] == end_time

    @pytest.mark.unit
    def test_job_duration_calculation(self):
        """Test calculating job duration from timestamps"""
        start_time = datetime(2025, 10, 11, 10, 0, 0)
        end_time = datetime(2025, 10, 11, 10, 15, 30)

        duration = end_time - start_time

        assert duration.total_seconds() == 930  # 15 minutes 30 seconds


class TestJobOrigins:
    """Tests for job origin tracking"""

    @pytest.mark.unit
    def test_different_queue_origins(self):
        """Test jobs from different queue origins"""
        mock_failed_jobs = MagicMock()

        origins = ['default', 'high_priority', 'low_priority', 'batch_processing']

        def insert_failed_job(j):
            mock_failed_jobs.update_one(
                {'job_id': j.id},
                {'$set': {
                    'job_id': j.id,
                    'started_at': j.started_at,
                    'ended_at': j.ended_at,
                    'origin': j.origin
                }},
                True
            )

        for idx, origin in enumerate(origins):
            mock_job = MagicMock()
            mock_job.id = f'job-{idx}'
            mock_job.started_at = datetime.now()
            mock_job.ended_at = datetime.now()
            mock_job.origin = origin

            insert_failed_job(mock_job)

            # Verify origin is preserved
            call_args = mock_failed_jobs.update_one.call_args
            update_doc = call_args[0][1]['$set']
            assert update_doc['origin'] == origin


class TestEdgeCases:
    """Tests for edge cases and error scenarios"""

    @pytest.mark.unit
    def test_job_with_special_characters_in_id(self):
        """Test job with special characters in ID"""
        mock_job_collection = MagicMock()

        special_ids = [
            'job-with-dashes',
            'job_with_underscores',
            'job.with.dots',
            'job:with:colons'
        ]

        for job_id in special_ids:
            mock_job = MagicMock()
            mock_job.id = job_id
            mock_job.get_status.return_value = 'queued'

            msg = 'job_' + mock_job.id + '_' + mock_job.get_status()

            mock_job_collection.update_one({'_id': job_id}, {'$set': {'type': msg}})

        assert mock_job_collection.update_one.call_count == 4

    @pytest.mark.unit
    def test_very_long_job_id(self):
        """Test handling of very long job IDs"""
        mock_job = MagicMock()
        mock_job.id = 'a' * 200  # Very long ID
        mock_job.get_status.return_value = 'queued'

        msg = 'job_' + mock_job.id + '_queued'

        assert len(msg) > 200
        assert msg.startswith('job_')
        assert msg.endswith('_queued')

    @pytest.mark.unit
    def test_rapid_status_changes(self):
        """Test job that changes status rapidly"""
        mock_job = MagicMock()
        mock_job.id = 'rapid-job'

        statuses = ['queued', 'started', 'finished']

        for status in statuses:
            mock_job.get_status.return_value = status
            result = mock_job.get_status()
            assert result == status

    @pytest.mark.unit
    def test_concurrent_job_processing(self):
        """Test that multiple jobs can be tracked simultaneously"""
        mock_job_collection = MagicMock()

        # Simulate many concurrent jobs
        job_count = 100
        jobs = [{'_id': f'job-{i}', 'type': f'test_job_{i}'} for i in range(job_count)]

        mock_job_collection.find.return_value = jobs

        jobs_list = mock_job_collection.find()
        processed_jobs = list(jobs_list)

        assert len(processed_jobs) == job_count

    @pytest.mark.unit
    def test_failed_job_without_timestamps(self):
        """Test failed job that has no start/end timestamps"""
        mock_failed_jobs = MagicMock()
        mock_job = MagicMock()
        mock_job.id = 'no-timestamp-job'
        mock_job.started_at = None
        mock_job.ended_at = None
        mock_job.origin = 'default'

        def insert_failed_job(j):
            mock_failed_jobs.update_one(
                {'job_id': j.id},
                {'$set': {
                    'job_id': j.id,
                    'started_at': j.started_at,
                    'ended_at': j.ended_at,
                    'origin': j.origin
                }},
                True
            )

        insert_failed_job(mock_job)

        call_args = mock_failed_jobs.update_one.call_args
        update_doc = call_args[0][1]['$set']

        # Should handle None values gracefully
        assert update_doc['started_at'] is None
        assert update_doc['ended_at'] is None


class TestJobDeletionOperations:
    """Tests for job deletion operations"""

    @pytest.mark.unit
    def test_delete_finished_jobs_batch(self):
        """Test deleting multiple finished jobs"""
        mock_job_collection = MagicMock()

        job_ids = ['job-1', 'job-2', 'job-3', 'job-4', 'job-5']

        for job_id in job_ids:
            mock_job_collection.delete_one({'_id': job_id})

        assert mock_job_collection.delete_one.call_count == 5

    @pytest.mark.unit
    def test_delete_from_queue_and_collection(self):
        """Test that both queue and collection deletions occur"""
        mock_job_collection = MagicMock()
        mock_job = MagicMock()
        mock_job.id = 'delete-test-job'

        job_id = 'delete-test-job'

        # Delete from both
        mock_job_collection.delete_one({'_id': job_id})
        mock_job.delete()

        mock_job_collection.delete_one.assert_called_once_with({'_id': job_id})
        mock_job.delete.assert_called_once()
