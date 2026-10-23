"""Cancelling a job the operator can see in the activity panel.

captureui has always had a Cancel button, and the backend has always proxied it
to DELETE :5001/cancel_job/<id> - but no route here ever received it. Every
cancel 404'd, and because the backend logs 'Job cancelled' and returns
`status_code < 300` without checking, the activity log recorded a cancellation
that never happened and the button appeared to do nothing.

Cancel has to work in three states, because all three reach the operator as one
stuck row:

  started    the work-horse is running - only the worker can stop it
  queued     still on the queue, so removing it from the queue is enough
  orphaned   the job hash outlived the queue entry (a worker popped it and died
             before marking it started). Nothing will ever run it and nothing
             will ever clear it, which is exactly the row that prompts a cancel.

The mongo record goes in all three, including when rq has nothing to stop. To
the operator Cancel means "clear this row"; leaving it because rq had already
forgotten the job is how a row becomes permanent.
"""
from flask_restx import Resource
from redis import Redis
from rq.job import Job

import auth
from worker_scripts.job_manager import job_collection

redis_con = Redis('localhost', 6379, password=None)


def stop_queued_or_running(job_id, connection=None):
    """Take the job out of rq. True if a running work-horse was told to stop.

    Every step is best-effort and independent: a job that cannot be stopped
    must not prevent the one that can, and none of them may stop the mongo
    record being cleared.
    """
    connection = connection or redis_con

    try:
        job = Job.fetch(job_id, connection=connection)
    except Exception:
        return False

    stopped = False
    try:
        if job.get_status() == 'started':
            # Only the worker can kill its own work-horse; cancel() raises on a
            # started job rather than stopping it.
            from rq.command import send_stop_job_command
            send_stop_job_command(connection, job_id)
            stopped = True
    except Exception as exc:
        print('[cancel_job] could not stop running job {}: {}'.format(job_id, exc))

    try:
        job.cancel()
    except Exception:
        # Already finished, already cancelled, or mid-stop.
        pass

    try:
        job.delete()
    except Exception:
        pass

    return stopped


class CancelJob(Resource):
    @auth.requires_auth
    def delete(self, job_id):
        tracked = job_collection.find_one({'_id': job_id})
        stopped = stop_queued_or_running(job_id)
        removed = job_collection.delete_one({'_id': job_id}).deleted_count

        if not tracked and not stopped:
            return {'cancelled': False, 'id': job_id,
                    'error': 'no such job'}, 404

        return {'cancelled': True, 'id': job_id,
                'stopped_worker': stopped, 'cleared': bool(removed)}, 200


def register_routes(api):
    api.add_resource(CancelJob, '/cancel_job/<string:job_id>')
