"""Single-run issue analysis using a process-held, database-specific lock."""
import hashlib
import logging
import os
import tomllib
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from workbench.issues.analysis_models import IssueAnalysis, IssueAttachment
from workbench.issues.models import Issue
from workbench.projects.models import Project
from workbench.questions.agent import AgentError, load_agent
from workbench.questions.library import ProjectLibrary
from workbench.issues.read_tools import AnalysisReadTools

logger = logging.getLogger('workbench.issue_analysis')


@contextmanager
def analysis_lock(engine):
    """Keep the lock until the agent exits; OS/database releases it on crash."""
    if engine.dialect.name == 'postgresql':
        with engine.connect() as connection:
            acquired = connection.scalar(text('SELECT pg_try_advisory_lock(1213024305, 1)'))
            connection.commit()
            try:
                yield (bool(acquired), connection.connection.driver_connection.fileno() if acquired and os.name != 'nt' else None)
            finally:
                if acquired:
                    connection.execute(text('SELECT pg_advisory_unlock(1213024305, 1)'))
                    connection.commit()
        return
    if engine.dialect.name != 'sqlite' or not engine.url.database or engine.url.database == ':memory:':
        raise RuntimeError('Analysis requires a file-backed SQLite or PostgreSQL database')
    database = Path(engine.url.database).resolve()
    path = database.with_name(database.name + '.analysis.lock')
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if os.name == 'nt':
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b'0')
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError:
                acquired = False
        else:
            import fcntl
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                acquired = False
        try:
            yield (acquired, fd if acquired and os.name != 'nt' else None)
        finally:
            if acquired:
                if os.name == 'nt':
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def process_analysis_one(engine, storage_root: Path, config_path: Path | None) -> bool:
    with analysis_lock(engine) as (acquired, lock_fd):
        if not acquired:
            return False
        with Session(engine) as db, db.begin():
            # On POSIX the child inherits the lock, including across worker crashes.
            if os.name == 'nt' and db.scalar(select(IssueAnalysis.id).where(IssueAnalysis.state == 'running').limit(1)):
                return False
            db.execute(update(IssueAnalysis).where(IssueAnalysis.state == 'running').values(state='queued'))
            run = db.scalar(select(IssueAnalysis).where(IssueAnalysis.state == 'queued')
                            .order_by(IssueAnalysis.created_at, IssueAnalysis.id).limit(1))
            if run is None:
                return False
            run.state = 'running'
            run_id, issue_id = run.id, run.issue_id
        try:
            with Session(engine) as db:
                row = db.execute(select(Issue, IssueAttachment, Project)
                                 .join(IssueAttachment, IssueAttachment.issue_id == Issue.id)
                                 .join(Project, Project.id == Issue.project_id)
                                 .where(Issue.id == issue_id)).first()
                if row is None:
                    raise AgentError('issue_missing')
                issue, attachment, project = row
                project_id, owner_id = project.id, project.owner
                module, description = attachment.module, issue.description
                log_path, filename, expected_digest = storage_root / attachment.object_key, attachment.filename, attachment.sha256
            digest = hashlib.sha256()
            size = 0
            with log_path.open('rb') as source:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > 10 * 1024 * 1024:
                        raise AgentError('log_too_large')
                    digest.update(chunk)
            if digest.hexdigest() != expected_digest:
                raise AgentError('log_integrity_failed')
            if config_path is None:
                raise AgentError('model_not_configured')
            with config_path.open('rb') as source:
                config = tomllib.load(source)
            settings = config.get('analysis', {})
            code_root = settings.get('code_root', '') if isinstance(settings, dict) else ''
            skill_root = settings.get('skill_root', '') if isinstance(settings, dict) else ''
            if not isinstance(code_root, str) or not code_root or not Path(code_root).is_dir():
                raise AgentError('code_root_unavailable')
            if not isinstance(skill_root, str) or (skill_root and not Path(skill_root).is_dir()):
                raise AgentError('skill_root_unavailable')
            agent = load_agent(config_path)
            if agent is None:
                raise AgentError('model_not_configured')
            library = ProjectLibrary(engine, owner_id, project_id, storage_root, 50 * 1024 * 1024)
            tools = AnalysisReadTools(code_root=Path(code_root), log_path=log_path, log_filename=filename,
                                      skill_root=Path(skill_root) if skill_root else None, library=library)
            tools.lock_fd = lock_fd
            answer = agent.analyze(f'功能模块：{module}\\n问题现象：{description}\\n请分析日志和代码，说明依据、原因和建议。', tools)
            verify = hashlib.sha256()
            size = 0
            with log_path.open('rb') as source:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > 10 * 1024 * 1024:
                        raise AgentError('log_too_large')
                    verify.update(chunk)
            if verify.hexdigest() != expected_digest:
                raise AgentError('log_integrity_failed')
            library.verify_catalog()
            library.verified_evidence()
            state, report, reason = 'completed', answer['answer'], None
        except AgentError as exc:
            logger.warning('analysis_failed analysis_id=%s issue_id=%s reason=%s', run_id, issue_id, exc.reason)
            state, report, reason = 'failed', None, exc.reason
        except Exception as exc:
            logger.error('analysis_failed analysis_id=%s issue_id=%s error_type=%s', run_id, issue_id, type(exc).__name__)
            state, report, reason = 'failed', None, 'resource_unavailable'
        with Session(engine) as db, db.begin():
            db.execute(update(IssueAnalysis).where(IssueAnalysis.id == run_id, IssueAnalysis.state == 'running')
                       .values(state=state, report=report, reason=reason))
        logger.info('analysis_finished analysis_id=%s issue_id=%s state=%s', run_id, issue_id, state)
        return True
