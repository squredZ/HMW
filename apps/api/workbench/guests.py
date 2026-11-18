"""Read-only, short-lived guest access to explicitly shared owner content."""
import json
import hashlib
import os
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from secrets import token_urlsafe
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from starlette.datastructures import UploadFile
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from workbench.audit import record_audit
from workbench.issues.models import Issue
from workbench.issues.analysis_models import IssueAttachment, IssueAnalysis
from workbench.knowledge.models import GuestPublication, KnowledgeVersion, SourceVersion
from workbench.knowledge.source_access import verified_download
from workbench.projects.models import Project, ProjectMember
from workbench.sessions import SessionStore
from workbench.questions.retrieval import guest_extract
from workbench.questions.conversation import ConversationStore, ConversationBusy
from workbench.questions.agent import AgentError, validate_answer
from workbench.questions.library import ProjectLibrary


question_logger = logging.getLogger('workbench.questions')


class GuestIssue(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    project_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=10000)


class GuestQuestion(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    project_id: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=2, max_length=240)
    conversation_id: UUID | None = None


def install_guest_routes(app: FastAPI, engine: Engine, dev_users: dict[str, str] | None,
                         object_root: Path, max_file_bytes: int, status_model,
                         status_version_model) -> None:
    # A single owner must be explicitly configured; no production identity is implied.
    owner_id = next(iter(dev_users)) if dev_users and len(dev_users) == 1 else None
    guests = SessionStore()
    conversations = ConversationStore()

    @app.middleware('http')
    async def prevent_guest_response_caching(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith('/api/guest/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    def require_guest(request: Request) -> str:
        if owner_id is None:
            raise HTTPException(404, 'Not found')
        session = guests.get(request.cookies.get('hmw_guest', ''), datetime.now(timezone.utc))
        if session is None:
            raise HTTPException(401, 'Guest session required')
        return session[0]

    @app.post('/api/guest/session')
    def start_guest(request: Request, response: Response):
        if owner_id is None:
            raise HTTPException(404, 'Not found')
        now = datetime.now(timezone.utc)
        token = request.cookies.get('hmw_guest', '')
        if guests.get(token, now) is None:
            token = token_urlsafe(32)
            guests.add(token, str(uuid4()), '', now + timedelta(hours=8), now)
        response.set_cookie('hmw_guest', token, httponly=True, samesite='strict')
        response.headers['Cache-Control'] = 'no-store'
        return {'kind':'guest'}

    @app.post('/api/guest/issues', status_code=201)
    async def submit_issue(request: Request):
        visitor_id = require_guest(request)
        upload = None
        is_multipart = request.headers.get('content-type', '').lower().startswith('multipart/form-data')
        if is_multipart:
            form = await request.form()
            project_id = str(form.get('project_id', '')).strip()
            module = str(form.get('module', '')).strip()
            description = str(form.get('description', '')).strip()
            upload = form.get('log')
            if (not project_id or len(project_id) > 64 or not module or len(module) > 200 or
                not description or len(description) > 10000 or not isinstance(upload, UploadFile)):
                raise HTTPException(422, 'Project, module, description and log are required')
            name = upload.filename or ''
            if not name or len(name) > 255 or name != Path(name).name or '\\' in name:
                raise HTTPException(422, 'Invalid log filename')
            title = description.splitlines()[0][:200]
        else:
            try:
                payload = GuestIssue.model_validate(await request.json())
            except (ValueError, ValidationError) as exc:
                raise HTTPException(422, 'Invalid issue') from exc
            project_id, title, description = payload.project_id, payload.title, payload.description

        with Session(engine) as db:
            project = db.scalar(select(Project.id).where(Project.id == project_id, Project.owner == owner_id))
            if project is None:
                raise HTTPException(404, 'Not found')
            if not is_multipart:
                visible = db.scalar(select(Project.id).join(KnowledgeVersion, KnowledgeVersion.project_id == Project.id)
                                    .join(GuestPublication, GuestPublication.version_id == KnowledgeVersion.id)
                                    .where(Project.id == project_id, Project.owner == owner_id,
                                           KnowledgeVersion.state == 'published').limit(1))
                if visible is None:
                    raise HTTPException(404, 'Not found')
        issue_id = str(uuid4())
        path = None
        committed = False
        try:
            if upload is not None:
                object_root.mkdir(parents=True, exist_ok=True, mode=0o700)
                key = uuid4().hex
                path = object_root / key
                digest = hashlib.sha256()
                size = 0
                with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as output:
                    while chunk := await upload.read(1024 * 1024):
                        size += len(chunk)
                        if size > 10 * 1024 * 1024:
                            raise HTTPException(413, 'Log exceeds size limit')
                        digest.update(chunk)
                        output.write(chunk)
                if not size:
                    raise HTTPException(422, 'Log is empty')
            with Session(engine) as db, db.begin():
                if not db.scalar(select(Project.id).where(Project.id == project_id, Project.owner == owner_id)):
                    raise HTTPException(404, 'Not found')
                db.add(Issue(id=issue_id, project_id=project_id, visitor_id=visitor_id, title=title,
                             description=description, state='new', version=1, created_at=datetime.now(timezone.utc)))
                if upload is not None:
                    db.add(IssueAttachment(issue_id=issue_id, module=module, filename=name, object_key=key, sha256=digest.hexdigest()))
                record_audit(db, actor_id=visitor_id, action='issue.submitted', resource_id=issue_id,
                             request_id=request.state.request_id, details={'project_id':project_id})
            committed = True
            return {'id':issue_id, 'state':'new'}
        finally:
            if upload is not None:
                await upload.close()
            if path is not None and not committed:
                path.unlink(missing_ok=True)

    @app.get('/api/guest/issues')
    def list_guest_issues(request: Request, limit: int = 50, offset: int = 0):
        require_guest(request)
        if not 1 <= limit <= 100 or offset < 0:
            raise HTTPException(422, 'Invalid pagination')
        with Session(engine) as db:
            rows = db.execute(select(Issue, Project.name, IssueAttachment)
                              .join(Project, Project.id == Issue.project_id)
                              .outerjoin(IssueAttachment, IssueAttachment.issue_id == Issue.id)
                              .where(Project.owner == owner_id)
                              .order_by(Issue.created_at.desc(), Issue.id.desc()).offset(offset).limit(limit + 1)).all()
            ids = [issue.id for issue, _, _ in rows[:limit]]
            ranked = (select(IssueAnalysis.id,
                             func.row_number().over(partition_by=IssueAnalysis.issue_id,
                                                    order_by=(IssueAnalysis.created_at.desc(), IssueAnalysis.id.desc())).label('position'))
                      .where(IssueAnalysis.issue_id.in_(ids)).subquery())
            analyses = db.scalars(select(IssueAnalysis).join(ranked, ranked.c.id == IssueAnalysis.id)
                                  .where(ranked.c.position == 1)).all()
            latest = {analysis.issue_id:{'state':analysis.state, 'report':analysis.report, 'reason':analysis.reason}
                      for analysis in analyses}
            return {'items':[{'id':issue.id, 'project_id':issue.project_id, 'project_name':name,
                              'module':attachment.module if attachment else '', 'description':issue.description,
                              'log_filename':attachment.filename if attachment else '', 'state':issue.state,
                              'analysis':latest.get(issue.id)} for issue, name, attachment in rows[:limit]],
                    'has_more':len(rows) > limit}

    @app.post('/api/guest/issues/{issue_id}/analyze', status_code=202)
    def request_analysis(issue_id: str, request: Request):
        visitor_id = require_guest(request)
        job_id = str(uuid4())
        try:
            with Session(engine) as db, db.begin():
                item = db.execute(select(Issue, IssueAttachment).join(Project, Project.id == Issue.project_id)
                                  .outerjoin(IssueAttachment, IssueAttachment.issue_id == Issue.id)
                                  .where(Issue.id == issue_id, Project.owner == owner_id)).first()
                if item is None:
                    raise HTTPException(404, 'Not found')
                if item[1] is None:
                    raise HTTPException(409, 'Issue has no log')
                db.add(IssueAnalysis(id=job_id, issue_id=issue_id, state='queued',
                                     created_at=datetime.now(timezone.utc).timestamp()))
                record_audit(db, actor_id=visitor_id, action='issue.analysis.requested', resource_id=issue_id,
                             request_id=request.state.request_id, details={'analysis_id':job_id})
        except IntegrityError as exc:
            raise HTTPException(409, 'Analysis already queued or running') from exc
        return {'id':job_id, 'state':'queued'}

    @app.post('/api/guest/questions')
    def ask_question(payload: GuestQuestion, request: Request):
        visitor = require_guest(request)
        agent = getattr(app.state, 'answer_agent', None)
        conversation_key = (visitor, payload.project_id, str(payload.conversation_id)) if payload.conversation_id else None
        history = []
        if conversation_key:
            try:
                history = conversations.begin(conversation_key)
            except ConversationBusy as exc:
                raise HTTPException(409, 'Conversation is busy') from exc
        finished = False
        try:
            if agent is not None and hasattr(agent, 'explore'):
                library = ProjectLibrary(engine, owner_id, payload.project_id, object_root, max_file_bytes)
                try:
                    # Previous answers may cite sources that have since been withdrawn.
                    safe_history = [{'question':turn['question'],
                                     'answer':turn['answer'] if library.history_is_current(turn.get('sources', [])) else ''}
                                    for turn in history]
                    generated = agent.explore(payload.question, safe_history, library)
                    require_guest(request)
                    library.verify_catalog()
                    if any(turn['answer'] and not library.history_is_current(original.get('sources', []))
                           for turn, original in zip(safe_history, history)):
                        raise HTTPException(409, 'Knowledge availability changed; retry')
                    evidence = library.verified_evidence()
                    candidate = validate_answer(generated, len(evidence), getattr(agent, 'max_answer_chars', 4000))
                    result = {'kind':'generated', 'message':candidate['answer'],
                              'citations':candidate['citations'], 'ungrounded':not bool(candidate['citations']),
                              'evidence':evidence, 'agent_invoked':True, 'retrieval_mode':'agent',
                              'search_limited':len(evidence) >= library.MAX_READS, 'unavailable_sources':0}
                    if conversation_key:
                        sources = [(item['knowledge_id'], item['source_sha256']) for item in evidence]
                        conversations.finish(conversation_key, payload.question, result['message'], sources)
                        finished = True
                    question_logger.info('guest_agent_completed request_id=%s project_id=%s reads=%s',
                                         request.state.request_id, payload.project_id, len(evidence))
                    return result
                except AgentError as exc:
                    question_logger.warning('agent_answer_failed request_id=%s reason=%s', request.state.request_id, exc.reason)
                    # Preserve the existing verified, extractive fallback.
                    require_guest(request)
                    agent = None
                    agent_failed = True
                    agent_error = exc.reason if exc.reason in {'timeout', 'busy', 'context_limit'} else 'unavailable'
            else:
                agent_failed = False
            search_query = payload.question
            if history:
                search_query += ' ' + ' '.join(turn['question'] for turn in history[-2:])
            result = guest_extract(engine, owner_id, payload.project_id, search_query, object_root, max_file_bytes,
                                   include_context=agent is not None or agent_failed, project_published=agent is not None or agent_failed,
                                   priority_question=payload.question)
            if agent_failed:
                result['agent_unavailable'] = True
                result['agent_invoked'] = True
                result['agent_error'] = agent_error
            if agent is not None and result['evidence']:
                result['agent_invoked'] = True
                candidate = None
                current_sources = {(item['knowledge_id'], item['source_sha256']) for item in result['evidence']}
                safe_history = [{'question':turn['question'],
                                 'answer':turn['answer'] if set(turn.get('sources', [])) <= current_sources else ''}
                                for turn in history]
                try:
                    generated = agent.answer(payload.question, result['evidence'], safe_history) if safe_history else agent.answer(payload.question, result['evidence'])
                    candidate = validate_answer(generated, len(result['evidence']))
                except AgentError as exc:
                    question_logger.warning('agent_answer_failed request_id=%s reason=%s', request.state.request_id, exc.reason)
                    result['agent_unavailable'] = True
                require_guest(request)
                try:
                    current = guest_extract(engine, owner_id, payload.project_id, search_query, object_root, max_file_bytes,
                                            include_context=True, project_published=True, priority_question=payload.question)
                except HTTPException as exc:
                    if exc.status_code != 404:
                        raise
                    raise HTTPException(409, 'Knowledge availability changed; retry') from exc
                def content_identity(items):
                    return [{key:value for key, value in item.items() if key != 'guest_visible'} for item in items]
                if content_identity(current['evidence']) != content_identity(result['evidence']) or current['retrieval_mode'] != result['retrieval_mode']:
                    raise HTTPException(409, 'Knowledge availability changed; retry')
                # Download visibility is independent of question access; use the latest flag.
                result['evidence'] = current['evidence']
                if candidate is not None:
                    if candidate['citations']:
                        result['kind'] = 'generated'
                        result['message'] = candidate['answer']
                        result['citations'] = candidate['citations']
                    else:
                        result['kind'] = 'insufficient_evidence'
                        result['message'] = '现有资料不足以回答，请补充信息或联系负责人。'
                        result['evidence'] = []
            if conversation_key:
                sources = [(item['knowledge_id'], item['source_sha256']) for item in result['evidence']]
                conversations.finish(conversation_key, payload.question, result['message'], sources)
                finished = True
            question_logger.info(json.dumps({
                'timestamp':datetime.now(timezone.utc).isoformat(), 'component':'questions',
                'event':'guest_search_completed', 'request_id':request.state.request_id,
                'project_id':payload.project_id, 'result':result['kind'],
                'evidence_count':len(result['evidence']), 'search_limited':result['search_limited'],
                'unavailable_sources':result['unavailable_sources'],
            }, ensure_ascii=False))
            return result
        finally:
            if conversation_key and not finished:
                conversations.abort(conversation_key)

    @app.get('/api/guest/status')
    def guest_status(request: Request):
        require_guest(request)
        with Session(engine) as db:
            row = db.scalar(select(status_model).where(status_model.owner == owner_id))
            if row is None:
                raise HTTPException(404, 'Not found')
            latest = db.scalar(select(status_version_model).where(
                status_version_model.status_id == row.id,
                status_version_model.state.in_(['published','withdrawn']),
            ).order_by(status_version_model.version.desc()).limit(1))
            if latest is None or latest.state != 'published' or latest.body.get('guest_visible') is not True:
                raise HTTPException(404, 'Not found')
            # Only explicitly selected project allocations can be shared.
            allowed = set(latest.body.get('audience_project_ids', []))
            projects = {project.id:project.name for project in db.scalars(
                select(Project).where(Project.owner == owner_id, Project.id.in_(allowed))
            )}
            allocations = [{**allocation, 'project_name':projects[allocation['project_id']]}
                           for allocation in latest.body.get('allocations', [])
                           if allocation.get('project_id') in projects]
            # SQLite omits timezone metadata; all stored application timestamps are UTC.
            updated_at = latest.created_at
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            return {'focus':latest.body.get('focus', ''), 'progress':latest.body.get('progress'),
                    'updated_at':updated_at.astimezone(timezone.utc).isoformat(), 'allocations':allocations}

    @app.get('/api/guest/question-projects')
    def guest_question_projects(request: Request):
        require_guest(request)
        with Session(engine) as db:
            projects = db.scalars(select(Project).where(Project.owner == owner_id).order_by(Project.name, Project.id)).all()
            return [{'id':project.id, 'name':project.name} for project in projects]

    @app.get('/api/guest/projects')
    def guest_projects(request: Request):
        require_guest(request)
        with Session(engine) as db:
            projects = db.scalars(select(Project).join(KnowledgeVersion, KnowledgeVersion.project_id == Project.id)
                                  .join(GuestPublication, GuestPublication.version_id == KnowledgeVersion.id)
                                  .where(Project.owner == owner_id, KnowledgeVersion.state == 'published')
                                  .distinct().order_by(Project.name, Project.id)).all()
            return [{'id':p.id, 'name':p.name} for p in projects]

    @app.get('/api/guest/projects/{project_id}/knowledge')
    def guest_project_knowledge(project_id: str, request: Request):
        require_guest(request)
        with Session(engine) as db:
            rows = db.execute(select(KnowledgeVersion.id, SourceVersion.filename)
                              .join(GuestPublication, GuestPublication.version_id == KnowledgeVersion.id)
                              .join(SourceVersion, SourceVersion.id == KnowledgeVersion.source_id)
                              .join(Project, Project.id == KnowledgeVersion.project_id)
                              .where(Project.id == project_id, Project.owner == owner_id,
                                     SourceVersion.project_id == project_id,
                                     KnowledgeVersion.state == 'published')
                              .order_by(KnowledgeVersion.id).limit(100)).all()
            if not rows:
                raise HTTPException(404, 'Not found')
            return [{'id':row.id, 'filename':row.filename} for row in rows]

    def public_version(db: Session, version_id: str):
        return db.execute(select(KnowledgeVersion, SourceVersion)
                          .join(GuestPublication, GuestPublication.version_id == KnowledgeVersion.id)
                          .join(SourceVersion, SourceVersion.id == KnowledgeVersion.source_id)
                          .join(Project, Project.id == KnowledgeVersion.project_id)
                          .where(KnowledgeVersion.id == version_id, KnowledgeVersion.state == 'published',
                                 Project.owner == owner_id, SourceVersion.project_id == Project.id)).first()

    @app.get('/api/guest/knowledge/{version_id}')
    def guest_knowledge(version_id: str, request: Request):
        require_guest(request)
        with Session(engine) as db:
            row = public_version(db, version_id)
            if row is None:
                raise HTTPException(404, 'Not found')
            version, source = row
            return {'id':version.id, 'content':version.content, 'locator':version.locator,
                    'source':{'id':source.id, 'filename':source.filename, 'sha256':source.sha256}}

    @app.get('/api/guest/sources/{source_id}/download')
    def guest_source(source_id: str, request: Request):
        require_guest(request)
        with Session(engine) as db:
            source = db.scalar(select(SourceVersion)
                               .join(KnowledgeVersion, KnowledgeVersion.source_id == SourceVersion.id)
                               .join(GuestPublication, GuestPublication.version_id == KnowledgeVersion.id)
                               .join(Project, Project.id == KnowledgeVersion.project_id)
                               .where(SourceVersion.id == source_id, KnowledgeVersion.state == 'published',
                                      SourceVersion.project_id == Project.id, Project.owner == owner_id).limit(1))
            if source is None:
                raise HTTPException(404, 'Not found')
            key, filename, digest = source.object_key, source.filename, source.sha256
        return verified_download(object_root, key, filename, digest, max_file_bytes)
