import json
from redis import Redis
from .config import REDIS_URL, TTL
r = Redis.from_url(REDIS_URL, decode_responses=True)
def get(kind, id):
    value = r.get(f'{kind}:{id}')
    return json.loads(value) if value else None
def put(kind, id, value):
    r.set(f'{kind}:{id}', json.dumps(value), ex=TTL)
def update(id, **changes):
    job = get('job', id)
    if job:
        job.update(changes)
        put('job', id, job)
    return job
