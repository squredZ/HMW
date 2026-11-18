"""Single-run issue analysis using a process-held, database-specific lock."""
import hashlib
import logging
import os
import tomllib
import time
import threading
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from workbench.issues.analysis_models import IssueAnalysis, IssueAnalysisUsage, IssueAttachment
from workbench.issues.models import Issue, IssueDetail
from workbench.projects.models import Project
from workbench.questions.agent import AgentError, load_agent
from workbench.questions.library import ProjectLibrary
from workbench.issues.read_tools import AnalysisReadTools
from workbench.logging_utils import log_event, safe_exception_frames
from workbench.audit import AuditEvent

logger = logging.getLogger('workbench.issue_analysis')
_windows_warning_lock = threading.Lock()
_last_windows_warning = 0.0


def verify_log(log_path: Path, expected_digest: str) -> None:
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


def process_analysis_one(engine, storage_root: Path, config_path: Path | None, worker_id: str | None = None) -> bool:
    with analysis_lock(engine) as (acquired, lock_fd):
        if not acquired:
            return False
        with Session(engine) as db, db.begin():
            # On POSIX the child inherits the lock, including across worker crashes.
            stale_id = db.scalar(select(IssueAnalysis.id).where(IssueAnalysis.state == 'running').limit(1)) if os.name == 'nt' else None
            if stale_id:
                global _last_windows_warning
                with _windows_warning_lock:
                    now = time.monotonic()
                    if now - _last_windows_warning >= 60:
                        _last_windows_warning = now
                        log_event(logger, logging.WARNING, 'issue_analysis', 'analysis_recovery_blocked',
                                  analysis_id=stale_id, worker_id=worker_id, reason='manual_recovery_required')
                return False
            recovered = db.execute(update(IssueAnalysis).where(IssueAnalysis.state == 'running').values(state='queued')).rowcount
            run = db.scalar(select(IssueAnalysis).where(IssueAnalysis.state == 'queued')
                            .order_by(IssueAnalysis.created_at, IssueAnalysis.id).limit(1))
            if run is None:
                return False
            run.state = 'running'
            run_id, issue_id, queued_at = run.id, run.issue_id, run.created_at
            request_id = db.scalar(select(AuditEvent.request_id).where(
                AuditEvent.action == 'issue.analysis.requested', AuditEvent.resource_id == issue_id,
                AuditEvent.details['analysis_id'].as_string() == run_id).limit(1))
        if recovered:
            log_event(logger, logging.WARNING, 'issue_analysis', 'analysis_recovered', worker_id=worker_id, count=recovered)
        started = time.monotonic()
        log_event(logger, logging.INFO, 'issue_analysis', 'analysis_started', analysis_id=run_id,
                  issue_id=issue_id, request_id=request_id, worker_id=worker_id, queue_wait_ms=max(0, round((time.time() - queued_at) * 1000, 2)))
        project_id = None
        tools = None
        try:
            with Session(engine) as db:
                row = db.execute(select(Issue, IssueAttachment, IssueDetail, Project)
                                 .outerjoin(IssueAttachment, IssueAttachment.issue_id == Issue.id)
                                 .outerjoin(IssueDetail, IssueDetail.issue_id == Issue.id)
                                 .join(Project, Project.id == Issue.project_id)
                                 .where(Issue.id == issue_id)).first()
                if row is None:
                    raise AgentError('issue_missing')
                issue, attachment, detail, project = row
                project_id, owner_id = project.id, project.owner
                module, description = detail.module if detail else attachment.module if attachment else '', issue.description
                log_path = storage_root / attachment.object_key if attachment else None
                filename = attachment.filename if attachment else None
                expected_digest = attachment.sha256 if attachment else None
            if log_path is not None:
                verify_log(log_path, expected_digest)
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
            tools.log_context = {'request_id':request_id, 'project_id':project_id,
                                 'analysis_id':run_id, 'issue_id':issue_id, 'worker_id':worker_id}
            log_context = '已提供日志，请按需读取。' if log_path else '未提供日志，不得声称已检查日志。'
            answer = agent.analyze(f'功能模块：{module}\\n问题现象：{description}\\n{log_context}请分析代码和可用资料，说明依据、原因和建议。', tools)
            if log_path is not None:
                verify_log(log_path, expected_digest)
            library.verify_catalog()
            library.verified_evidence()
            state, report, reason = 'completed', answer['answer'], None
        except AgentError as exc:
            log_event(logger, logging.WARNING, 'issue_analysis', 'analysis_failed', analysis_id=run_id,
                      issue_id=issue_id, project_id=project_id, request_id=request_id, worker_id=worker_id, reason=exc.reason)
            state, report, reason = 'failed', None, exc.reason
        except Exception as exc:
            log_event(logger, logging.ERROR, 'issue_analysis', 'analysis_failed', analysis_id=run_id,
                      issue_id=issue_id, project_id=project_id, request_id=request_id, worker_id=worker_id,
                      error_type=type(exc).__name__, frames=safe_exception_frames(exc))
            state, report, reason = 'failed', None, 'resource_unavailable'
        try:
            with Session(engine) as db, db.begin():
                updated = db.execute(update(IssueAnalysis).where(IssueAnalysis.id == run_id, IssueAnalysis.state == 'running')
                                     .values(state=state, report=report, reason=reason)).rowcount
                usage = getattr(tools, 'token_usage', None)
                if updated == 1 and usage is not None:
                    if state == 'failed':
                        usage = {**usage, 'partial': True}
                    existing = db.get(IssueAnalysisUsage, run_id)
                    if existing is None:
                        db.add(IssueAnalysisUsage(analysis_id=run_id, usage=usage))
                    else:
                        existing.usage = usage
        except Exception as exc:
            log_event(logger, logging.ERROR, 'issue_analysis', 'analysis_completion_failed', analysis_id=run_id,
                      issue_id=issue_id, project_id=project_id, request_id=request_id, worker_id=worker_id,
                      error_type=type(exc).__name__, frames=safe_exception_frames(exc))
            raise
        if updated != 1:
            log_event(logger, logging.ERROR, 'issue_analysis', 'analysis_completion_rejected',
                      analysis_id=run_id, issue_id=issue_id, project_id=project_id, request_id=request_id, worker_id=worker_id)
            return True
        log_event(logger, logging.INFO, 'issue_analysis', 'analysis_finished', analysis_id=run_id,
                  issue_id=issue_id, project_id=project_id, request_id=request_id, worker_id=worker_id, state=state, reason=reason,
                  duration_ms=round((time.monotonic() - started) * 1000, 2))
        return True
