import os
import sys
settings_path = os.environ['HOME']+'/flex-run'
sys.path.append(settings_path)

import redis
from rq import Worker, Queue, Connection

listen = ["default"]
redis_url = 'redis://localhost:6379'
conn = redis.from_url(redis_url)

if __name__ == '__main__':
    with Connection(conn):
        worker = Worker(list(map(Queue, listen)))
        worker.work(with_scheduler=True)
