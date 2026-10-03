"""Cancelling a job, in every state the operator can catch one in.

The Cancel button in captureui proxies to DELETE :5001/cancel_job/<id>. There
was no route here to answer it, so every cancel 404'd while the backend logged
a cancellation that never happened - these pin the route down and the three
states a stuck row can be in.
"""
import pytest
from unittest.mock import patch, MagicMock

from routes import job_routes


@pytest.fixture
def client():
    from flask import Flask
    from flask_restx import Api
    app = Flask(__name__)
    app.config['TESTING'] = True
    api = Api(app)
    job_routes.register_routes(api)
    return app.test_client()


@pytest.fixture(autouse=True)
def no_auth():
    with patch('auth.requires_auth', lambda f: f):
        yield


@pytest.fixture
def jobs():
    """The mongo side: one tracked job."""
    collection = MagicMock()
    collection.find_one.return_value = {'_id': 'job-1', 'status': 'running'}
    collection.delete_one.return_value = MagicMock(deleted_count=1)
    with patch.object(job_routes, 'job_collection', collection):
        yield collection


def _rq_job(status):
    job = MagicMock()
    job.get_status.return_value = status
    return job


class TestCancelQueued:
    @pytest.mark.integration
    def test_a_queued_job_is_taken_off_the_queue(self, client, jobs):
        job = _rq_job('queued')
        with patch.object(job_routes.Job, 'fetch', return_value=job):
            response = client.delete('/cancel_job/job-1')
        assert response.status_code == 200
        assert response.json['cancelled'] is True
        job.cancel.assert_called_once()

    @pytest.mark.integration
    def test_the_tracked_record_is_cleared(self, client, jobs):
        with patch.object(job_routes.Job, 'fetch', return_value=_rq_job('queued')):
            client.delete('/cancel_job/job-1')
        jobs.delete_one.assert_called_once_with({'_id': 'job-1'})


class TestCancelRunning:
    @pytest.mark.integration
    def test_a_started_job_is_stopped_through_the_worker(self, client, jobs):
        """cancel() raises on a started job - only the worker can stop one."""
        job = _rq_job('started')
        stop = MagicMock()
        with patch.object(job_routes.Job, 'fetch', return_value=job), \
             patch('rq.command.send_stop_job_command', stop):
            response = client.delete('/cancel_job/job-1')
        assert response.json['stopped_worker'] is True
        assert stop.call_args[0][1] == 'job-1'

    @pytest.mark.integration
    def test_a_worker_that_will_not_stop_still_clears_the_row(self, client, jobs):
        """Otherwise the one case an operator cannot resolve stays on screen."""
        job = _rq_job('started')
        with patch.object(job_routes.Job, 'fetch', return_value=job), \
             patch('rq.command.send_stop_job_command',
                   side_effect=Exception('no such worker')):
            response = client.delete('/cancel_job/job-1')
        assert response.status_code == 200
        jobs.delete_one.assert_called_once_with({'_id': 'job-1'})


class TestCancelOrphaned:
    """The state that produced the stuck row: a job hash that outlived its
    queue entry, so nothing runs it and nothing clears it."""

    @pytest.mark.integration
    def test_a_job_rq_has_forgotten_is_still_cleared(self, client, jobs):
        from rq.exceptions import NoSuchJobError
        with patch.object(job_routes.Job, 'fetch', side_effect=NoSuchJobError):
            response = client.delete('/cancel_job/job-1')
        assert response.status_code == 200
        assert response.json['cancelled'] is True
        jobs.delete_one.assert_called_once_with({'_id': 'job-1'})

    @pytest.mark.integration
    def test_an_unknown_job_is_a_404(self, client, jobs):
        from rq.exceptions import NoSuchJobError
        jobs.find_one.return_value = None
        jobs.delete_one.return_value = MagicMock(deleted_count=0)
        with patch.object(job_routes.Job, 'fetch', side_effect=NoSuchJobError):
            response = client.delete('/cancel_job/nope')
        assert response.status_code == 404


class TestRegistration:
    @pytest.mark.integration
    def test_the_path_the_backend_proxies_to_is_registered(self):
        """The backend hardcodes this path; it is the contract between them."""
        from flask import Flask
        from flask_restx import Api
        app = Flask(__name__)
        job_routes.register_routes(Api(app))
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert '/cancel_job/<string:job_id>' in rules

    @pytest.mark.integration
    def test_it_is_registered_by_the_app(self):
        import routes
        assert hasattr(routes, 'job_routes')
