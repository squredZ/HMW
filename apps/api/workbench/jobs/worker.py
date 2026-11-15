"""Independent text-import worker; all results are fenced and transactional."""
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections.abc import Callable
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from workbench.jobs.models import Job
from workbench.jobs.queue import JobQueue, StaleLease
from workbench.knowledge.models import KnowledgeVersion, SourceVersion
from workbench.knowledge.parsers.office import ParseBlocked, parse_office
from workbench.projects.models import ProjectMember

logger = logging.getLogger('workbench.worker')


class SourceTooLarge(Exception):
    """The source exceeds the configured read budget."""


def _read_source(path: Path, max_bytes: int, chunk_bytes: int, queue: JobQueue,
                 lease, clock: Callable[[], datetime]) -> tuple[bytes, str]:
    data = bytearray()
    digest = hashlib.sha256()
    next_heartbeat = lease.expires_at - timedelta(seconds=queue.lease_seconds - 15)
    with path.open('rb') as source_file:
        while True:
            chunk = source_file.read(min(chunk_bytes, max_bytes - len(data) + 1))
            if not chunk:
                break
            if len(data) + len(chunk) > max_bytes:
                raise SourceTooLarge()
            data.extend(chunk)
            digest.update(chunk)
            current = clock()
            if current >= next_heartbeat:
                lease = queue.heartbeat(lease, current)
                next_heartbeat = current + timedelta(seconds=15)
    return bytes(data), digest.hexdigest()


def process_one(engine: Engine, storage_root: Path, worker_id: str,
                now: datetime | None = None, *, max_source_bytes: int = 50 * 1024 * 1024,
                clock: Callable[[], datetime] | None = None,
                chunk_bytes: int = 1024 * 1024) -> bool:
    if max_source_bytes <= 0 or chunk_bytes <= 0:
        raise ValueError('read limits must be positive')
    clock = clock or (lambda: datetime.now(timezone.utc))
    now = now or clock()
    queue = JobQueue(engine)
    lease = queue.claim(worker_id, now, kinds=('import_text',))
    if lease is None:
        return False

    # Copy metadata and release the database connection before filesystem I/O.
    with Session(engine) as db:
        job = db.get(Job, lease.job_id)
        requester_id, requester_project = job.payload['actor_id'], job.payload['project_id']
        source = db.get(SourceVersion, job.payload['source_id'])
        membership = db.get(ProjectMember, (requester_project, requester_id))
        allowed = membership is not None and membership.role in ('owner', 'maintainer')
        source_data = (source.id, source.project_id, source.object_key, source.sha256, source.filename) if source else None

    content = None
    if not allowed:
        state, result = 'failed', {'reason':'permission_revoked'}
    elif source_data is None:
        state, result = 'failed', {'reason':'source_missing'}
    else:
        source_id, project_id, object_key, expected_digest, filename = source_data
        try:
            raw, actual_digest = _read_source(Path(storage_root) / object_key, max_source_bytes,
                                              chunk_bytes, queue, lease, clock)
            if actual_digest != expected_digest:
                state, result = 'failed', {'reason':'source_integrity_failed'}
            else:
                suffix = Path(filename).suffix.lower()
                if suffix in ('.docx', '.pptx', '.doc', '.ppt'):
                    parsed = parse_office(raw, filename)
                    content, locator = parsed.content, parsed.locator
                else:
                    content = raw.decode('utf-8-sig')
                    if '\x00' in content:
                        raise UnicodeError('binary content')
                    locator = {'start_line':1, 'end_line':len(content.splitlines())}
                state, result = 'completed', {'knowledge_id':str(uuid4())}
        except SourceTooLarge:
            state, result = 'failed', {'reason':'source_too_large'}
        except ParseBlocked as exc:
            content, state, result = None, 'needs_input', {'reason':str(exc)}
        except UnicodeError:
            content, state, result = None, 'needs_input', {'reason':'encoding_unrecognized'}
        except StaleLease:
            logger.warning('stale import lease during read; job_id=%s', lease.job_id)
            return True
        except OSError:
            logger.exception('text import source read failed; job_id=%s', lease.job_id)
            state, result = 'failed', {'reason':'source_unavailable'}

    with Session(engine) as db, db.begin():
        # Lock membership through the fenced commit, so revocation and completion
        # have a defined order on databases supporting row-level locks.
        membership = db.scalar(select(ProjectMember).where(
            ProjectMember.project_id == requester_project,
            ProjectMember.user_id == requester_id,
        ).with_for_update())
        if membership is None or membership.role not in ('owner', 'maintainer'):
            content, state, result = None, 'failed', {'reason':'permission_revoked'}
        finished_at = max(now, clock())
        updated = db.execute(update(Job).where(*queue._owned(lease, finished_at.timestamp()))
                             .values(state=state, result=result, expires_at=None)).rowcount
        if updated != 1:
            logger.warning('stale import completion discarded; job_id=%s', lease.job_id)
            return True
        if content is not None:
            db.add(KnowledgeVersion(id=result['knowledge_id'], source_id=source_id,
                                    project_id=project_id, content=content, state='draft',
                                    locator=locator))
    logger.info(json.dumps({
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'component': 'worker', 'event': 'import_finished',
        'job_id': lease.job_id, 'project_id': requester_project,
        'state': state, 'reason': result.get('reason'),
    }, ensure_ascii=False))
    return True

def main() -> None:
    from sqlalchemy import create_engine
    from workbench.db import Base

    logging.basicConfig(level=logging.INFO, format='%(message)s')
    database_url = os.environ.get('HMW_DATABASE_URL', 'sqlite:///./workbench-dev.db')
    root = Path(os.environ.get('HMW_STORAGE_ROOT', './workbench-objects'))
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    worker_id = str(uuid4())
    try:
        while True:
            if not process_one(engine, root, worker_id):
                time.sleep(1)
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
