"""Atomic job claims and fenced completion across independent worker processes."""
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from workbench.jobs.models import Job


class StaleLease(Exception):
    """The job is no longer owned by this unexpired lease."""


@dataclass(frozen=True)
class Lease:
    job_id: str
    worker_id: str
    token: int
    expires_at: datetime


def _timestamp(value: datetime) -> float:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('time must be timezone-aware')
    return value.timestamp()


class JobQueue:
    def __init__(self, engine: Engine, lease_seconds: int = 60):
        if lease_seconds <= 0:
            raise ValueError('lease_seconds must be positive')
        self.engine = engine
        self.lease_seconds = lease_seconds

    def submit(self, kind: str, payload: dict) -> str:
        job_id = str(uuid4())
        with Session(self.engine) as db, db.begin():
            db.add(Job(id=job_id, kind=kind, payload=payload, state='queued',
                       created_at=datetime.now(timezone.utc).timestamp(), token=0))
        return job_id

    def claim(self, worker_id: str, now: datetime, *, kinds: tuple[str, ...] | None = None) -> Lease | None:
        current = _timestamp(now)
        expiry = current + self.lease_seconds
        eligible = or_(Job.state == 'queued', and_(Job.state == 'running', Job.expires_at <= current))
        if kinds is not None:
            eligible = and_(eligible, Job.kind.in_(kinds))
        candidate = select(Job.id).where(eligible).order_by(Job.created_at, Job.id).limit(1).scalar_subquery()
        statement = (update(Job).where(Job.id == candidate, eligible)
                     .values(state='running', worker_id=worker_id, expires_at=expiry, token=Job.token + 1)
                     .returning(Job.id, Job.token))
        with Session(self.engine) as db, db.begin():
            claimed = db.execute(statement).first()
            if claimed is None:
                return None
            return Lease(claimed.id, worker_id, claimed.token,
                         datetime.fromtimestamp(expiry, timezone.utc))

    def heartbeat(self, lease: Lease, now: datetime) -> Lease:
        current = _timestamp(now)
        expiry = current + self.lease_seconds
        with Session(self.engine) as db, db.begin():
            updated = db.execute(update(Job).where(*self._owned(lease, current))
                                 .values(expires_at=expiry)).rowcount
            if updated != 1:
                raise StaleLease()
        return Lease(lease.job_id, lease.worker_id, lease.token,
                     datetime.fromtimestamp(expiry, timezone.utc))

    def complete(self, lease: Lease, result: dict, now: datetime) -> None:
        current = _timestamp(now)
        with Session(self.engine) as db, db.begin():
            updated = db.execute(update(Job).where(*self._owned(lease, current))
                                 .values(state='completed', result=result, expires_at=None)).rowcount
            if updated != 1:
                raise StaleLease()

    def get(self, job_id: str) -> dict | None:
        with Session(self.engine) as db:
            job = db.get(Job, job_id)
            if job is None:
                return None
            return {'id': job.id, 'kind': job.kind, 'state': job.state, 'result': job.result}

    @staticmethod
    def _owned(lease: Lease, current: float):
        return (Job.id == lease.job_id, Job.state == 'running',
                Job.worker_id == lease.worker_id, Job.token == lease.token,
                Job.expires_at > current)
