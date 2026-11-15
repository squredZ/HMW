"""Independent text-import worker; all results are fenced and transactional."""
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from workbench.jobs.models import Job
from workbench.jobs.queue import JobQueue
from workbench.knowledge.models import KnowledgeVersion, SourceVersion
from workbench.projects.models import ProjectMember

logger = logging.getLogger('workbench.worker')


def process_one(engine: Engine, storage_root: Path, worker_id: str, now: datetime | None = None, *, max_source_bytes: int = 50 * 1024 * 1024) -> bool:
    if max_source_bytes <= 0:
        raise ValueError('max_source_bytes must be positive')
    now = now or datetime.now(timezone.utc)
    queue = JobQueue(engine)
    lease = queue.claim(worker_id, now, kinds=('import_text',))
    if lease is None:
        return False
    with Session(engine) as db:
        job = db.get(Job, lease.job_id)
        requester_id, requester_project = job.payload['actor_id'], job.payload['project_id']
        source = db.get(SourceVersion, job.payload['source_id'])
        membership = db.get(ProjectMember, (job.payload['project_id'], job.payload['actor_id']))
        if membership is None or membership.role not in ('owner', 'maintainer'):
            content, state, result = None, 'failed', {'reason':'permission_revoked'}
        elif source is None:
            content, state, result = None, 'failed', {'reason':'source_missing'}
        else:
            try:
                with (Path(storage_root) / source.object_key).open('rb') as source_file:
                    raw = source_file.read(max_source_bytes + 1)
                if len(raw) > max_source_bytes:
                    content, state, result = None, 'failed', {'reason':'source_too_large'}
                elif hashlib.sha256(raw).hexdigest() != source.sha256:
                    content, state, result = None, 'failed', {'reason':'source_integrity_failed'}
                else:
                    content = raw.decode('utf-8-sig')
                    if '\x00' in content:
                        raise UnicodeError('binary content')
                    state, result = 'completed', {'knowledge_id':str(uuid4())}
            except UnicodeError:
                content, state, result = None, 'needs_input', {'reason':'encoding_unrecognized'}
            except OSError:
                logger.exception('text import source read failed; job_id=%s', lease.job_id)
                content, state, result = None, 'failed', {'reason':'source_unavailable'}
            source_id, project_id = source.id, source.project_id
    with Session(engine) as db, db.begin():
        # Lock membership through the fenced commit, so revocation and completion
        # have a defined order on databases supporting row-level locks.
        membership = db.scalar(select(ProjectMember).where(
            ProjectMember.project_id == requester_project,
            ProjectMember.user_id == requester_id,
        ).with_for_update())
        if membership is None or membership.role not in ('owner', 'maintainer'):
            content, state, result = None, 'failed', {'reason':'permission_revoked'}
        # Re-read time after waiting for the membership lock, not before it.
        finished_at = max(now, datetime.now(timezone.utc))
        updated = db.execute(update(Job).where(*queue._owned(lease, finished_at.timestamp()))
                             .values(state=state, result=result, expires_at=None)).rowcount
        if updated != 1:
            logger.warning('stale import completion discarded; job_id=%s', lease.job_id)
            return True
        if content is not None:
            lines = len(content.splitlines())
            db.add(KnowledgeVersion(id=result['knowledge_id'], source_id=source_id,
                                    project_id=project_id, content=content, state='draft',
                                    locator={'start_line':1, 'end_line':lines}))
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
