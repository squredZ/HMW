"""Authorized text imports, knowledge previews, publication and source downloads."""
import hashlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, File, Header, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from workbench.audit import record_audit
from workbench.logging_utils import log_event
from workbench.jobs.models import Job
from workbench.knowledge.models import GuestPublication, KnowledgeVersion, SourceVersion
from workbench.knowledge.source_access import verified_download
from workbench.projects.models import ProjectMember


logger = logging.getLogger('workbench.knowledge')


class GuestVisibilityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visible: bool


class PublishBatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_ids: list[str] = Field(min_length=1, max_length=100)
    guest_visible: bool = False


def install_knowledge_routes(app: FastAPI, engine: Engine, actor, object_root: Path,
                             max_file_bytes: int, max_batch_files: int) -> None:
    def project_access(db: Session, project_id: str, user: str, *, maintain: bool = False) -> None:
        query = select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user)
        membership = db.scalar(query.with_for_update() if maintain else query)
        if membership is None or (maintain and membership.role not in ("owner", "maintainer")):
            raise HTTPException(404, "Not found")

    @app.post("/api/projects/{project_id}/imports", status_code=202)
    async def import_text(project_id: str, request: Request, files: list[UploadFile] = File(...), x_user_id: str | None = Header(default=None), x_principal_type: str | None = Header(default=None), x_csrf_token: str | None = Header(default=None)):
        user = actor(request, x_user_id, x_principal_type, x_csrf_token, True)
        if len(files) > max_batch_files:
            raise HTTPException(413, "Too many files in batch")
        with Session(engine) as db:
            project_access(db, project_id, user, maintain=True)
        staged: list[tuple[str, str, str, str]] = []
        paths: list[Path] = []
        committed = False
        try:
            object_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            for upload in files:
                name = upload.filename or ""
                if not name or name != Path(name).name or "\\" in name or Path(name).suffix.lower() not in (".md", ".txt", ".docx", ".pptx", ".doc", ".ppt") or len(name) > 255:
                    raise HTTPException(422, "Unsupported document format")
                source_id, key = str(uuid4()), uuid4().hex
                path = object_root / key
                digest = hashlib.sha256()
                size = 0
                with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as out:
                    paths.append(path)
                    while chunk := await upload.read(1024 * 1024):
                        size += len(chunk)
                        if size > max_file_bytes:
                            raise HTTPException(413, "File exceeds configured size limit")
                        digest.update(chunk)
                        out.write(chunk)
                if size == 0:
                    raise HTTPException(422, "Empty files cannot be imported")
                staged.append((source_id, key, name, digest.hexdigest()))
            with Session(engine) as db, db.begin():
                project_access(db, project_id, user, maintain=True)
                jobs = []
                for source_id, key, name, digest in staged:
                    job_id = str(uuid4())
                    db.add(SourceVersion(id=source_id, project_id=project_id, filename=name, object_key=key, sha256=digest))
                    db.add(Job(id=job_id, kind="import_text", payload={"source_id":source_id,"project_id":project_id,"actor_id":user,"request_id":request.state.request_id}, state="queued", created_at=datetime.now(timezone.utc).timestamp(), token=0))
                    record_audit(db, actor_id=user, action="knowledge.import.submitted", resource_id=source_id, request_id=request.state.request_id, details={"project_id":project_id, "job_id":job_id})
                    jobs.append(job_id)
            committed = True
            log_event(logger, logging.INFO, 'knowledge', 'imports_queued',
                      request_id=request.state.request_id, project_id=project_id, job_ids=jobs,
                      source_ids=[source_id for source_id, _, _, _ in staged], count=len(jobs))
            return {"jobs": jobs}
        finally:
            for upload in files:
                await upload.close()
            if not committed:
                for path in paths:
                    path.unlink(missing_ok=True)

    @app.get("/api/projects/{project_id}/imports")
    def list_imports(project_id: str, request: Request, limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=10000), x_user_id: str | None = Header(default=None), x_principal_type: str | None = Header(default=None)):
        user = actor(request, x_user_id, x_principal_type, None)
        with Session(engine) as db:
            project_access(db, project_id, user, maintain=True)
            rows = db.execute(
                select(Job, SourceVersion.filename)
                .join(SourceVersion, Job.payload["source_id"].as_string() == SourceVersion.id)
                .where(Job.kind == "import_text", SourceVersion.project_id == project_id,
                       Job.payload["project_id"].as_string() == project_id)
                .order_by(Job.created_at.desc(), Job.id.desc()).offset(offset).limit(limit)
            ).all()
            return {"jobs": [{"id":job.id, "state":job.state, "filename":filename,
                              "created_at":datetime.fromtimestamp(job.created_at, timezone.utc).isoformat()}
                             for job, filename in rows]}

    @app.get("/api/imports/{job_id}")
    def get_import(job_id: str, request: Request, x_user_id: str | None = Header(default=None), x_principal_type: str | None = Header(default=None)):
        user = actor(request, x_user_id, x_principal_type, None)
        with Session(engine) as db:
            job = db.get(Job, job_id)
            if job is None or job.kind != "import_text":
                raise HTTPException(404, "Not found")
            project_access(db, job.payload["project_id"], user, maintain=True)
            return {"id":job.id, "state":job.state, "result":job.result}

    @app.get("/api/projects/{project_id}/knowledge")
    def list_knowledge(project_id: str, request: Request, state: Literal["draft", "published", "retired"] | None = None,
                       q: str = Query(default="", max_length=255), limit: int = Query(default=50, ge=1, le=100),
                       offset: int = Query(default=0, ge=0, le=10000), x_user_id: str | None = Header(default=None),
                       x_principal_type: str | None = Header(default=None)):
        user = actor(request, x_user_id, x_principal_type, None)
        with Session(engine) as db:
            project_access(db, project_id, user, maintain=True)
            query = (select(KnowledgeVersion.id, KnowledgeVersion.state, SourceVersion.filename,
                            GuestPublication.version_id.is_not(None).label("guest_visible"))
                     .join(SourceVersion, KnowledgeVersion.source_id == SourceVersion.id)
                     .outerjoin(GuestPublication, GuestPublication.version_id == KnowledgeVersion.id)
                     .where(KnowledgeVersion.project_id == project_id, SourceVersion.project_id == project_id))
            if state:
                query = query.where(KnowledgeVersion.state == state)
            if q:
                query = query.where(SourceVersion.filename.contains(q, autoescape=True))
            rows = db.execute(query.order_by(KnowledgeVersion.id).offset(offset).limit(limit + 1)).all()
            return {"items": [{"id": row.id, "state": row.state, "filename": row.filename,
                               "guest_visible": row.guest_visible} for row in rows[:limit]],
                    "has_more": len(rows) > limit}

    @app.post("/api/knowledge/{version_id}/retire")
    def retire_knowledge(version_id: str, request: Request, x_user_id: str | None = Header(default=None),
                         x_principal_type: str | None = Header(default=None), x_csrf_token: str | None = Header(default=None)):
        user = actor(request, x_user_id, x_principal_type, x_csrf_token, True)
        with Session(engine) as db, db.begin():
            version = db.get(KnowledgeVersion, version_id)
            if version is None:
                raise HTTPException(404, "Not found")
            project_access(db, version.project_id, user, maintain=True)
            result = db.execute(update(KnowledgeVersion).where(KnowledgeVersion.id == version_id,
                                KnowledgeVersion.state == "published").values(state="retired"))
            if result.rowcount != 1:
                raise HTTPException(409, "Only published knowledge can be retired")
            db.execute(delete(GuestPublication).where(GuestPublication.version_id == version_id))
            record_audit(db, actor_id=user, action="knowledge.retired", resource_id=version_id,
                         request_id=request.state.request_id, details={"project_id": version.project_id})
        return {"version_id": version_id, "state": "retired"}

    @app.get("/api/knowledge/{version_id}")
    def get_knowledge(version_id: str, request: Request, x_user_id: str | None = Header(default=None), x_principal_type: str | None = Header(default=None)):
        user = actor(request, x_user_id, x_principal_type, None)
        with Session(engine) as db:
            version = db.get(KnowledgeVersion, version_id)
            if version is None:
                raise HTTPException(404, "Not found")
            project_access(db, version.project_id, user, maintain=version.state != "published")
            source = db.get(SourceVersion, version.source_id)
            shared = db.get(GuestPublication, version.id) is not None
            return {"id":version.id, "project_id":version.project_id, "state":version.state,
                    "content":version.content, "locator":version.locator, "guest_visible":shared,
                    "source": {"id":source.id, "filename":source.filename, "sha256":source.sha256}}

    @app.get("/api/sources/{source_id}/download")
    def download_source(source_id: str, request: Request, x_user_id: str | None = Header(default=None), x_principal_type: str | None = Header(default=None)):
        user = actor(request, x_user_id, x_principal_type, None)
        with Session(engine) as db:
            source = db.get(SourceVersion, source_id)
            if source is None:
                raise HTTPException(404, "Not found")
            project_access(db, source.project_id, user)
            published = db.scalar(select(KnowledgeVersion.id).where(
                KnowledgeVersion.source_id == source_id, KnowledgeVersion.state == "published"
            ).limit(1))
            if published is None:
                project_access(db, source.project_id, user, maintain=True)
            filename, digest, key = source.filename, source.sha256, source.object_key
        return verified_download(object_root, key, filename, digest, max_file_bytes)

    @app.put("/api/knowledge/{version_id}/guest-visibility")
    def set_guest_visibility(version_id: str, payload: GuestVisibilityInput, request: Request, x_user_id: str | None = Header(default=None), x_principal_type: str | None = Header(default=None), x_csrf_token: str | None = Header(default=None)):
        user = actor(request, x_user_id, x_principal_type, x_csrf_token, True)
        with Session(engine) as db, db.begin():
            version = db.get(KnowledgeVersion, version_id)
            if version is None:
                raise HTTPException(404, "Not found")
            project_access(db, version.project_id, user, maintain=True)
            version = db.scalar(select(KnowledgeVersion).where(KnowledgeVersion.id == version_id).with_for_update())
            if version is None or version.state != "published":
                raise HTTPException(409, "Only published knowledge can be shared")
            db.execute(delete(GuestPublication).where(GuestPublication.version_id == version_id))
            if payload.visible:
                db.add(GuestPublication(version_id=version_id))
            record_audit(db, actor_id=user, action="knowledge.guest_visibility.changed", resource_id=version_id,
                         request_id=request.state.request_id,
                         details={"project_id":version.project_id, "guest_visible":payload.visible})
        return {"version_id":version_id, "guest_visible":payload.visible}

    @app.post("/api/knowledge/publish-batch")
    def publish_knowledge(payload: PublishBatchInput, request: Request, x_user_id: str | None = Header(default=None), x_principal_type: str | None = Header(default=None), x_csrf_token: str | None = Header(default=None)):
        user = actor(request, x_user_id, x_principal_type, x_csrf_token, True)
        if len(set(payload.version_ids)) != len(payload.version_ids):
            raise HTTPException(422, "Duplicate version")
        with Session(engine) as db, db.begin():
            versions = {version.id: version for version in db.scalars(
                select(KnowledgeVersion).where(KnowledgeVersion.id.in_(payload.version_ids))
            )}
            if len(versions) != len(payload.version_ids):
                raise HTTPException(404, "Not found")
            # Stable lock order avoids opposite-order multi-project batch deadlocks.
            for project_id in sorted({version.project_id for version in versions.values()}):
                project_access(db, project_id, user, maintain=True)
            for version_id in sorted(payload.version_ids):
                version = versions[version_id]
                result = db.execute(update(KnowledgeVersion).where(KnowledgeVersion.id == version_id, KnowledgeVersion.state == "draft").values(state="published"))
                if result.rowcount != 1:
                    raise HTTPException(409, "Version is not a draft")
                if payload.guest_visible:
                    db.add(GuestPublication(version_id=version_id))
                record_audit(db, actor_id=user, action="knowledge.published", resource_id=version_id, request_id=request.state.request_id, details={"project_id":version.project_id, "guest_visible":payload.guest_visible})
        return {"version_ids":payload.version_ids}
