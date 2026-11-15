from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from workbench.db import Base
from workbench.jobs.queue import JobQueue, StaleLease


@pytest.fixture
def queue(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.db'}", connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    return JobQueue(engine)


def test_claim_is_persistent_and_completed_result_survives_restart(queue):
    now = datetime(2026, 11, 15, tzinfo=timezone.utc)
    job_id = queue.submit('import_text', {'source_id':'example'})
    lease = queue.claim('worker-a', now)
    assert lease.job_id == job_id
    queue.complete(lease, {'knowledge_id':'draft-1'}, now + timedelta(seconds=1))
    reloaded = JobQueue(queue.engine).get(job_id)
    assert reloaded['state'] == 'completed'
    assert reloaded['result'] == {'knowledge_id':'draft-1'}
    assert queue.claim('worker-b', now + timedelta(seconds=2)) is None


def test_only_one_worker_claims_a_job(queue):
    now = datetime(2026, 11, 15, tzinfo=timezone.utc)
    queue.submit('import_text', {'source_id':'example'})
    barrier = Barrier(2, timeout=5)
    def claim(worker):
        barrier.wait()
        return queue.claim(worker, now)
    with ThreadPoolExecutor(max_workers=2) as pool:
        leases = list(pool.map(claim, ['worker-a', 'worker-b']))
    assert sum(lease is not None for lease in leases) == 1


def test_expired_worker_is_fenced_even_after_reclaim(queue):
    now = datetime(2026, 11, 15, tzinfo=timezone.utc)
    job_id = queue.submit('import_text', {'source_id':'example'})
    old = queue.claim('old-worker', now)
    assert queue.claim('new-worker', now + timedelta(seconds=59)) is None
    new = queue.claim('new-worker', now + timedelta(seconds=60))
    assert new.job_id == job_id and new.token > old.token
    with pytest.raises(StaleLease):
        queue.complete(old, {'incorrect':True}, now + timedelta(seconds=61))
    with pytest.raises(StaleLease):
        queue.heartbeat(old, now + timedelta(seconds=61))
    queue.complete(new, {'correct':True}, now + timedelta(seconds=61))
    assert queue.get(job_id)['result'] == {'correct':True}


def test_heartbeat_extends_lease_and_expired_leases_cannot_complete(queue):
    now = datetime(2026, 11, 15, tzinfo=timezone.utc)
    queue.submit('import_text', {'source_id':'example'})
    lease = queue.claim('worker', now)
    renewed = queue.heartbeat(lease, now + timedelta(seconds=30))
    assert renewed.expires_at == now + timedelta(seconds=90)
    assert queue.claim('other', now + timedelta(seconds=89)) is None
    with pytest.raises(StaleLease):
        queue.complete(lease, {}, now + timedelta(seconds=90))
