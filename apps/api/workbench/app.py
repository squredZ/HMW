"""Development API for the personal status slice.

The header-based identity provider exists exclusively in explicit test mode.
Production requests are rejected until an approved identity provider is configured.
"""
import logging
import os
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from secrets import compare_digest, token_urlsafe
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import JSON, DateTime, Integer, String, create_engine, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.orm.exc import StaleDataError

from workbench.audit import record_audit
from workbench.logging_utils import log_event
from workbench.issues.models import Issue
from workbench.issues.analysis_models import IssueAttachment, IssueAnalysis
from workbench.issues.body_limit import IssueBodyLimit
from workbench.db import Base
from workbench.request_logging import install_request_logging
from workbench.projects.models import Project, ProjectMember
from workbench.sessions import SessionStore
from workbench.knowledge.routes import install_knowledge_routes
from workbench.guests import install_guest_routes
from workbench.knowledge.upload_limit import UploadLimitMiddleware


class ProjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)


class MemberInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["member", "maintainer"]


class Status(Base):
    __tablename__ = "personal_status"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # The version participates in UPDATE predicates even on SQLite, where
    # SELECT FOR UPDATE does not lock rows. The service increments it.
    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}


class StatusVersion(Base):
    __tablename__ = "personal_status_version"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    status_id: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Allocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    week: date
    unit: Literal["hours", "percent"]
    planned: float | None = Field(default=None, ge=0)
    actual: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_percent(self):
        if self.unit == "percent" and any(v is not None and v > 100 for v in (self.planned, self.actual)):
            raise ValueError("percentage must be at most 100")
        return self


class StatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    focus: str = Field(default="", max_length=10000)
    progress: int | None = Field(default=None, ge=0, le=100)
    expected_version: int | None = None
    guest_visible: bool = False
    audience_project_ids: list[str] = Field(default_factory=list)
    allocations: list[Allocation] = Field(default_factory=list)


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str
    password: str


class PublishInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int


class IssueTransitionInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    state: Literal['needs_info', 'triaged', 'in_progress', 'resolved', 'closed']
    reason: str = Field(min_length=1, max_length=500)
    expected_version: int = Field(ge=1)


ISSUE_TRANSITIONS = {
    'new': {'needs_info', 'triaged'},
    'needs_info': {'triaged'},
    'triaged': {'needs_info', 'in_progress'},
    'in_progress': {'needs_info', 'resolved'},
    'resolved': {'in_progress', 'closed'},
    'closed': set(),
}

issue_logger = logging.getLogger('workbench.issues')


def create_app(database_url: str = "sqlite:///./workbench.db", testing: bool = False, dev_users: dict[str, str] | None = None, storage_root: str | Path | None = None, max_file_bytes: int = 50 * 1024 * 1024, max_batch_files: int = 20) -> FastAPI:
    if dev_users is not None and not testing and len(dev_users) != 1:
        raise ValueError("Exactly one development owner account is required")
    if max_file_bytes <= 0 or max_batch_files <= 0:
        raise ValueError("file limits must be positive")
    engine = create_engine(database_url, connect_args={"check_same_thread": False} if database_url.startswith("sqlite") else {})
    Base.metadata.create_all(engine)
    app = FastAPI(title="Workbench API")
    app.state.engine = engine
    object_root = Path(storage_root or os.environ.get("HMW_STORAGE_ROOT", "./workbench-objects"))

    app.add_middleware(IssueBodyLimit, max_bytes=10 * 1024 * 1024 + 128 * 1024)
    app.add_middleware(UploadLimitMiddleware, max_body_bytes=max_file_bytes * max_batch_files + 1024 * 1024)
    install_request_logging(app)
    sessions = SessionStore()

    @app.exception_handler(StaleDataError)
    def handle_stale_write(request: Request, exc: StaleDataError):
        return JSONResponse(status_code=409, content={"detail": "Version conflict"})

    def actor(request: Request, user: str | None, principal: str | None, csrf: str | None, write: bool = False) -> str:
        session = sessions.get(request.cookies.get("hmw_session", ""), datetime.now(timezone.utc))
        if session and session[2] > datetime.now(timezone.utc):
            if write and (csrf is None or not compare_digest(csrf, session[1])):
                raise HTTPException(403, "CSRF validation failed")
            return session[0]
        if not testing or not user:
            raise HTTPException(401, "Authenticated session required")
        if principal != "human_session":
            raise HTTPException(403, "Human session required")
        if write and csrf != "test":
            raise HTTPException(403, "CSRF validation failed")
        return user

    def snapshot(row: Status) -> dict:
        return {"id": row.id, "version": row.version, "state": row.state, "updated_at": row.updated_at.isoformat(), **row.body}

    def record(db: Session, row: Status):
        db.add(StatusVersion(id=str(uuid4()), status_id=row.id, version=row.version, state=row.state, body=row.body, created_at=row.updated_at))

    @app.post("/api/dev/session")
    def login(payload: LoginInput, response: Response):
        if dev_users is None:
            raise HTTPException(404, "Not found")
        expected = dev_users.get(payload.username)
        if expected is None or not compare_digest(expected, payload.password):
            raise HTTPException(401, "Invalid credentials")
        token, csrf = token_urlsafe(32), token_urlsafe(32)
        now = datetime.now(timezone.utc)
        sessions.add(token, payload.username, csrf, now + timedelta(hours=8), now)
        response.set_cookie("hmw_session", token, httponly=True, samesite="strict", max_age=8 * 3600)
        return {"csrf_token": csrf}

    @app.delete("/api/dev/session", status_code=204)
    def logout(request: Request, response: Response, x_csrf_token: str | None = Header(default=None)):
        actor(request, None, None, x_csrf_token, True)
        sessions.remove(request.cookies.get("hmw_session", ""))
        response.delete_cookie("hmw_session")

    @app.get("/api/projects")
    def list_projects(request: Request, x_user_id: str | None = Header(default=None), x_principal_type: str | None = Header(default=None)):
        user = actor(request, x_user_id, x_principal_type, None)
        with Session(engine) as db:
            rows = db.execute(select(Project, ProjectMember.role).join(ProjectMember, Project.id == ProjectMember.project_id).where(ProjectMember.user_id == user)).all()
            return [{"id": project.id, "name": project.name, "role": role} for project, role in rows]

    @app.post("/api/projects")
    def create_project(payload: ProjectInput, request: Request, x_user_id: str | None = Header(default=None), x_principal_type: str | None = Header(default=None), x_csrf_token: str | None = Header(default=None)):
        user = actor(request, x_user_id, x_principal_type, x_csrf_token, True)
        with Session(engine) as db, db.begin():
            project = Project(id=str(uuid4()), name=payload.name, owner=user)
            db.add(project)
            db.add(ProjectMember(project_id=project.id, user_id=user, role="owner"))
            record_audit(db, actor_id=user, action="project.created", resource_id=project.id,
                         request_id=request.state.request_id)
            return {"id": project.id, "name": project.name, "role": "owner"}

    if testing:
        @app.put("/api/projects/{project_id}/members/{member_id}")
        def set_member(project_id: str, member_id: str, payload: MemberInput, request: Request, x_user_id: str | None = Header(default=None), x_principal_type: str | None = Header(default=None), x_csrf_token: str | None = Header(default=None)):
            user = actor(request, x_user_id, x_principal_type, x_csrf_token, True)
            with Session(engine) as db, db.begin():
                project = db.get(Project, project_id)
                if not project:
                    raise HTTPException(404, "Not found")
                if project.owner != user or member_id == project.owner:
                    raise HTTPException(403, "Forbidden")
                member = db.scalar(select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == member_id).with_for_update())
                previous_role = member.role if member else None
                if member is None:
                    member = ProjectMember(project_id=project_id, user_id=member_id, role=payload.role)
                    db.add(member)
                else:
                    member.role = payload.role
                record_audit(db, actor_id=user, action="project.member.set", resource_id=project_id,
                             request_id=request.state.request_id,
                             details={"member_id": member_id, "old_role": previous_role, "new_role": payload.role})
                return {"user_id": member_id, "role": member.role}

        @app.delete("/api/projects/{project_id}/members/{member_id}", status_code=204)
        def remove_member(project_id: str, member_id: str, request: Request, x_user_id: str | None = Header(default=None), x_principal_type: str | None = Header(default=None), x_csrf_token: str | None = Header(default=None)):
            user = actor(request, x_user_id, x_principal_type, x_csrf_token, True)
            with Session(engine) as db, db.begin():
                project = db.get(Project, project_id)
                if not project:
                    raise HTTPException(404, "Not found")
                if project.owner != user or member_id == project.owner:
                    raise HTTPException(403, "Forbidden")
                member = db.scalar(select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == member_id).with_for_update())
                if member is None:
                    raise HTTPException(404, "Not found")
                record_audit(db, actor_id=user, action="project.member.removed", resource_id=project_id,
                             request_id=request.state.request_id,
                             details={"member_id": member_id, "old_role": member.role})
                db.delete(member)

    @app.get("/api/personal/status")
    def get_status(request: Request, x_user_id: str | None = Header(default=None), x_principal_type: str | None = Header(default=None)):
        user = actor(request, x_user_id, x_principal_type, None)
        with Session(engine) as db:
            row = db.scalar(select(Status).where(Status.owner == user))
            return snapshot(row) if row else None

    @app.get("/api/personal/status/{owner_id}")
    def get_published_status(owner_id: str, request: Request, x_user_id: str | None = Header(default=None), x_principal_type: str | None = Header(default=None)):
        user = actor(request, x_user_id, x_principal_type, None)
        with Session(engine) as db:
            row = db.scalar(select(Status).where(Status.owner == owner_id))
            if row is None:
                raise HTTPException(404, "Not found")
            published = db.scalar(select(StatusVersion).where(StatusVersion.status_id == row.id, StatusVersion.state.in_(["published", "withdrawn"])).order_by(StatusVersion.version.desc()).limit(1))
            if published is None or published.state == "withdrawn":
                raise HTTPException(404, "Not found")
            owner_projects = set(db.scalars(select(ProjectMember.project_id).where(ProjectMember.user_id == owner_id)).all())
            viewer_projects = set(db.scalars(select(ProjectMember.project_id).where(ProjectMember.user_id == user)).all())
            allowed = set(published.body.get("audience_project_ids", [])) & owner_projects & viewer_projects
            if not allowed:
                raise HTTPException(404, "Not found")
            body = dict(published.body)
            body.pop("audience_project_ids", None)
            body["allocations"] = [a for a in body.get("allocations", []) if a["project_id"] in allowed]
            return {"id": row.id, "version": published.version, "state": "published", "updated_at": published.created_at.isoformat(), **body}

    @app.put("/api/personal/status")
    def save_status(payload: StatusInput, request: Request, x_user_id: str | None = Header(default=None), x_principal_type: str | None = Header(default=None), x_csrf_token: str | None = Header(default=None)):
        user = actor(request, x_user_id, x_principal_type, x_csrf_token, True)
        with Session(engine) as db, db.begin():
            project_ids = set(payload.audience_project_ids) | {a.project_id for a in payload.allocations}
            for project_id in project_ids:
                if db.get(ProjectMember, (project_id, user)) is None:
                    raise HTTPException(403, "Project access required")
            row = db.scalar(select(Status).where(Status.owner == user).with_for_update())
            if row and payload.expected_version != row.version:
                raise HTTPException(409, "Version conflict")
            if not row:
                if payload.expected_version is not None:
                    raise HTTPException(409, "Version conflict")
                row = Status(id=str(uuid4()), owner=user, version=0, state="draft", body={}, updated_at=datetime.now(timezone.utc))
                db.add(row)
                try:
                    db.flush()
                except IntegrityError as exc:
                    # Only translate the owner uniqueness race; unrelated database
                    # errors must remain failures rather than false conflicts.
                    constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
                    if constraint == "personal_status_owner_key" or "UNIQUE constraint failed: personal_status.owner" in str(exc.orig):
                        raise HTTPException(409, "Version conflict") from exc
                    raise
            row.version += 1
            row.state = "draft"
            row.body = payload.model_dump(mode="json", exclude={"expected_version"})
            row.updated_at = datetime.now(timezone.utc)
            record(db, row)
            record_audit(db, actor_id=user, action="personal.draft.saved", resource_id=row.id,
                         resource_version=row.version, request_id=request.state.request_id)
            return snapshot(row)

    @app.post("/api/personal/status/{status_id}/publish")
    def publish_status(status_id: str, payload: PublishInput, request: Request, x_user_id: str | None = Header(default=None), x_principal_type: str | None = Header(default=None), x_csrf_token: str | None = Header(default=None)):
        user = actor(request, x_user_id, x_principal_type, x_csrf_token, True)
        with Session(engine) as db, db.begin():
            row = db.scalar(select(Status).where(Status.id == status_id).with_for_update())
            if not row or row.owner != user:
                raise HTTPException(404, "Not found")
            if row.version != payload.expected_version:
                raise HTTPException(409, "Version conflict")
            if row.state != "draft":
                raise HTTPException(409, "Only a draft can be published")
            row.version += 1
            row.state = "published"
            row.updated_at = datetime.now(timezone.utc)
            record(db, row)
            record_audit(db, actor_id=user, action="personal.published", resource_id=row.id,
                         resource_version=row.version, request_id=request.state.request_id)
            return snapshot(row)

    @app.post("/api/personal/status/{status_id}/withdraw")
    def withdraw_status(status_id: str, payload: PublishInput, request: Request, x_user_id: str | None = Header(default=None), x_principal_type: str | None = Header(default=None), x_csrf_token: str | None = Header(default=None)):
        user = actor(request, x_user_id, x_principal_type, x_csrf_token, True)
        with Session(engine) as db, db.begin():
            row = db.scalar(select(Status).where(Status.id == status_id).with_for_update())
            if not row or row.owner != user:
                raise HTTPException(404, "Not found")
            if row.version != payload.expected_version:
                raise HTTPException(409, "Version conflict")
            latest = db.scalar(select(StatusVersion).where(
                StatusVersion.status_id == row.id,
                StatusVersion.state.in_(["published", "withdrawn"]),
            ).order_by(StatusVersion.version.desc()).limit(1))
            if latest is None or latest.state == "withdrawn":
                raise HTTPException(409, "No published status to withdraw")
            row.version += 1
            row.state = "withdrawn"
            row.updated_at = datetime.now(timezone.utc)
            record(db, row)
            record_audit(db, actor_id=user, action="personal.withdrawn", resource_id=row.id,
                         resource_version=row.version, request_id=request.state.request_id)
            return snapshot(row)

    @app.get('/api/issues')
    def list_issues(request: Request, response: Response, limit: int = Query(default=50, ge=1, le=100),
                    offset: int = Query(default=0, ge=0, le=10000),
                    x_user_id: str | None = Header(default=None), x_principal_type: str | None = Header(default=None)):
        user = actor(request, x_user_id, x_principal_type, None)
        response.headers['Cache-Control'] = 'no-store'
        with Session(engine) as db:
            rows = db.execute(select(Issue, Project.name).join(Project, Project.id == Issue.project_id)
                              .where(Project.owner == user).order_by(Issue.created_at.desc(), Issue.id.desc())
                              .offset(offset).limit(limit + 1)).all()
            return {'items': [{'id':issue.id, 'project_id':issue.project_id, 'project_name':name,
                               'title':issue.title, 'description':issue.description, 'state':issue.state, 'version':issue.version,
                               'created_at':issue.created_at.isoformat()} for issue, name in rows[:limit]],
                    'has_more':len(rows) > limit}

    @app.post('/api/issues/{issue_id}/transition')
    def transition_issue(issue_id: str, payload: IssueTransitionInput, request: Request,
                         x_user_id: str | None = Header(default=None), x_principal_type: str | None = Header(default=None),
                         x_csrf_token: str | None = Header(default=None)):
        user = actor(request, x_user_id, x_principal_type, x_csrf_token, True)
        with Session(engine) as db, db.begin():
            issue = db.get(Issue, issue_id)
            if issue is None or not db.scalar(select(Project.id).where(Project.id == issue.project_id, Project.owner == user)):
                raise HTTPException(404, 'Not found')
            old_state, project_id = issue.state, issue.project_id
            if issue.version != payload.expected_version or payload.state not in ISSUE_TRANSITIONS.get(old_state, set()):
                raise HTTPException(409, 'Invalid transition or version conflict')
            changed = db.execute(update(Issue).where(Issue.id == issue_id, Issue.version == payload.expected_version,
                                                      Issue.state == old_state)
                                 .values(state=payload.state, version=Issue.version + 1)).rowcount
            if changed != 1:
                raise HTTPException(409, 'Version conflict')
            version = payload.expected_version + 1
            record_audit(db, actor_id=user, action='issue.transitioned', resource_id=issue_id,
                         resource_version=version, request_id=request.state.request_id,
                         details={'project_id':project_id, 'from_state':old_state,
                                  'to_state':payload.state, 'reason':payload.reason})
        log_event(issue_logger, logging.INFO, 'issues', 'issue_transitioned', request_id=request.state.request_id,
                  project_id=project_id, issue_id=issue_id, from_state=old_state, to_state=payload.state, version=version)
        return {'id':issue_id, 'state':payload.state, 'version':version}

    @app.post('/api/issues/{issue_id}/resolve')
    def resolve_issue(issue_id: str, request: Request, x_user_id: str | None = Header(default=None),
                      x_principal_type: str | None = Header(default=None), x_csrf_token: str | None = Header(default=None)):
        user = actor(request, x_user_id, x_principal_type, x_csrf_token, True)
        with Session(engine) as db, db.begin():
            issue = db.get(Issue, issue_id)
            if issue is None or not db.scalar(select(Project.id).where(Project.id == issue.project_id, Project.owner == user)):
                raise HTTPException(404, 'Not found')
            changed = db.execute(update(Issue).where(Issue.id == issue_id, Issue.state == 'new')
                                 .values(state='resolved', version=Issue.version + 1)).rowcount
            if changed != 1:
                raise HTTPException(409, 'Issue is not new')
            project_id, version = issue.project_id, issue.version
            record_audit(db, actor_id=user, action='issue.resolved', resource_id=issue_id,
                         resource_version=version, request_id=request.state.request_id,
                         details={'project_id':project_id, 'from_state':'new', 'to_state':'resolved', 'reason':'legacy_resolve'})
        log_event(issue_logger, logging.INFO, 'issues', 'issue_transitioned', request_id=request.state.request_id,
                  project_id=project_id, issue_id=issue_id, from_state='new', to_state='resolved', version=version)
        return {'id':issue_id, 'state':'resolved'}

    install_knowledge_routes(app, engine, actor, object_root, max_file_bytes, max_batch_files)
    install_guest_routes(app, engine, dev_users, object_root, max_file_bytes, Status, StatusVersion)
    return app


app = create_app()
