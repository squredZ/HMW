"""Read-only, short-lived guest access to explicitly shared owner content."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from secrets import token_urlsafe
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from workbench.knowledge.models import GuestPublication, KnowledgeVersion, SourceVersion
from workbench.knowledge.source_access import verified_download
from workbench.projects.models import Project
from workbench.sessions import SessionStore


def install_guest_routes(app: FastAPI, engine: Engine, dev_users: dict[str, str] | None,
                         object_root: Path, max_file_bytes: int, status_model,
                         status_version_model) -> None:
    # A single owner must be explicitly configured; no production identity is implied.
    owner_id = next(iter(dev_users)) if dev_users and len(dev_users) == 1 else None
    guests = SessionStore()

    @app.middleware('http')
    async def prevent_guest_response_caching(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith('/api/guest/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    def require_guest(request: Request) -> None:
        if owner_id is None:
            raise HTTPException(404, 'Not found')
        if guests.get(request.cookies.get('hmw_guest', ''), datetime.now(timezone.utc)) is None:
            raise HTTPException(401, 'Guest session required')

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
