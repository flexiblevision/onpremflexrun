"""Reconciliation between the rq queue and the mongo job records.

The previous version of this file tested a copy of the logic pasted into the
test body, so job_watcher.py itself was never imported and sat at 0% while the
file reported as covered. These exercise the module.

What matters here is that a finished job is removed exactly once, a failed job
is recorded before it is removed, and a job the queue has forgotten does not
leave a permanent record behind.
"""
import pytest
from unittest.mock import patch, MagicMock, call

import job_watcher as jw


@pytest.fixture
def queue():
    with patch.object(jw, 'job_queue') as q:
        yield q


@pytest.fixture
def jobs():
    with patch.object(jw, 'job_collection') as c:
        yield c


@pytest.fixture
def failed():
    with patch.object(jw, 'failed_jobs') as f:
        yield f


def _rq_job(job_id='job-1', status='finished'):
    j = MagicMock()
    j.id = job_id
    j.get_status.return_value = status
    j.started_at = '2025-08-25T09:00:00'
    j.ended_at = '2025-08-25T09:01:00'
    j.origin = 'default'
    return j


class TestInsertFailedJob:
    @pytest.mark.unit
    def test_records_the_failure_details(self, failed):
        jw.insert_failed_job(_rq_job('job-7', 'failed'))

        query, update, upsert = failed.update_one.call_args[0]
        assert query == {'job_id': 'job-7'}
        assert update['$set'] == {
            'job_id': 'job-7',
            'started_at': '2025-08-25T09:00:00',
            'ended_at': '2025-08-25T09:01:00',
            'origin': 'default',
        }

    @pytest.mark.unit
    def test_upserts_so_a_retried_job_is_not_duplicated(self, failed):
        jw.insert_failed_job(_rq_job('job-7', 'failed'))
        assert failed.update_one.call_args[0][2] is True


class TestReconcileJob:
    @pytest.mark.unit
    def test_a_finished_job_is_deleted_from_both_stores(self, queue, jobs, failed):
        j = _rq_job('job-1', 'finished')
        queue.fetch_job.return_value = j

        jw.reconcile_job({'_id': 'job-1'})

        jobs.delete_one.assert_called_once_with({'_id': 'job-1'})
        j.delete.assert_called_once()
        failed.update_one.assert_not_called()

    @pytest.mark.unit
    def test_a_failed_job_is_recorded_before_being_removed(self, queue, jobs, failed):
        j = _rq_job('job-2', 'failed')
        queue.fetch_job.return_value = j

        jw.reconcile_job({'_id': 'job-2'})

        failed.update_one.assert_called_once()
        jobs.delete_one.assert_called_once_with({'_id': 'job-2'})
        # The rq job is kept so its traceback stays retrievable.
        j.delete.assert_not_called()

    @pytest.mark.unit
    def test_a_running_job_is_left_alone(self, queue, jobs, failed):
        queue.fetch_job.return_value = _rq_job('job-3', 'started')

        jw.reconcile_job({'_id': 'job-3'})

        jobs.delete_one.assert_not_called()
        jobs.update_one.assert_not_called()
        failed.update_one.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.parametrize('status', ['queued', 'deferred', 'scheduled', 'stopped'])
    def test_any_other_status_is_published_onto_the_record(self, queue, jobs, status):
        queue.fetch_job.return_value = _rq_job('job-4', status)

        jw.reconcile_job({'_id': 'job-4'})

        jobs.update_one.assert_called_once_with(
            {'_id': 'job-4'}, {'$set': {'type': f'job_job-4_{status}'}})
        jobs.delete_one.assert_not_called()

    @pytest.mark.unit
    def test_a_job_the_queue_has_forgotten_is_skipped(self, queue, jobs, failed):
        # fetch_job returns None once the result has expired out of redis.
        queue.fetch_job.return_value = None

        jw.reconcile_job({'_id': 'job-5'})

        jobs.delete_one.assert_not_called()
        jobs.update_one.assert_not_called()
        failed.update_one.assert_not_called()

    @pytest.mark.unit
    def test_status_is_read_once_per_job(self, queue, jobs):
        # Each call is a redis round trip, and the loop runs twice a second
        # against every tracked job.
        j = _rq_job('job-6', 'queued')
        queue.fetch_job.return_value = j

        jw.reconcile_job({'_id': 'job-6'})

        assert j.get_status.call_count == 1

    @pytest.mark.unit
    def test_the_job_is_looked_up_by_its_mongo_id(self, queue, jobs):
        queue.fetch_job.return_value = None
        jw.reconcile_job({'_id': 'job-8'})
        queue.fetch_job.assert_called_once_with('job-8')


class TestReconcileOnce:
    @pytest.mark.unit
    def test_every_tracked_job_is_reconciled(self, jobs):
        jobs.find.return_value = [{'_id': 'a'}, {'_id': 'b'}, {'_id': 'c'}]

        with patch.object(jw, 'reconcile_job') as reconcile:
            jw.reconcile_once()

        assert reconcile.call_args_list == [
            call({'_id': 'a'}), call({'_id': 'b'}), call({'_id': 'c'})]

    @pytest.mark.unit
    def test_an_empty_collection_is_a_no_op(self, jobs):
        jobs.find.return_value = []
        with patch.object(jw, 'reconcile_job') as reconcile:
            jw.reconcile_once()
        reconcile.assert_not_called()

    @pytest.mark.unit
    def test_mixed_statuses_in_one_pass(self, queue, jobs, failed):
        jobs.find.return_value = [{'_id': 'done'}, {'_id': 'bad'}, {'_id': 'busy'}]
        queue.fetch_job.side_effect = [
            _rq_job('done', 'finished'),
            _rq_job('bad', 'failed'),
            _rq_job('busy', 'started'),
        ]

        jw.reconcile_once()

        assert jobs.delete_one.call_args_list == [
            call({'_id': 'done'}), call({'_id': 'bad'})]
        failed.update_one.assert_called_once()


class TestMainLoop:
    @pytest.mark.unit
    def test_polls_on_the_configured_interval(self, main_thread_sleep):
        ticks = []

        with patch('job_watcher.time.sleep',
                   side_effect=main_thread_sleep(ticks, 3)), \
             patch.object(jw, 'reconcile_once') as reconcile:
            with pytest.raises(KeyboardInterrupt):
                jw.main()

        assert ticks == [jw.POLL_INTERVAL] * 3
        assert reconcile.call_count == 2

    @pytest.mark.unit
    def test_importing_the_module_does_not_start_the_loop(self):
        # The loop used to run at module scope, which is why this module was
        # untestable and sat at 0%.
        import importlib
        with patch.object(jw, 'reconcile_once') as reconcile:
            importlib.reload(jw)
        reconcile.assert_not_called()
