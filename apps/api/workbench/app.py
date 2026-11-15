"""Development API for the personal status slice.

The header-based identity provider exists exclusively in explicit test mode.
Production requests are rejected until an approved identity provider is configured.
"""
from datetime import datetime, timezone, timedelta
from secrets import compare_digest, token_urlsafe
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import JSON, DateTime, Integer, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class Base(DeclarativeBase):
    pass


class Status(Base):
    __tablename__ = "personal_status"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StatusVersion(Base):
    __tablename__ = "personal_status_version"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    status_id: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    focus: str = Field(default="", max_length=10000)
    progress: int | None = Field(default=None, ge=0, le=100)
    expected_version: int | None = None


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str
    password: str


class PublishInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int


def create_app(database_url: str = "sqlite:///./workbench.db", testing: bool = False, dev_users: dict[str, str] | None = None) -> FastAPI:
    engine = create_engine(database_url, connect_args={"check_same_thread": False} if database_url.startswith("sqlite") else {})
    Base.metadata.create_all(engine)
    app = FastAPI(title="Workbench API")
    sessions: dict[str, tuple[str, str, datetime]] = {}

    def actor(request: Request, user: str | None, principal: str | None, csrf: str | None, write: bool = False) -> str:
        session = sessions.get(request.cookies.get("hmw_session", ""))
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
        sessions[token] = (payload.username, csrf, datetime.now(timezone.utc) + timedelta(hours=8))
        response.set_cookie("hmw_session", token, httponly=True, samesite="strict", max_age=8 * 3600)
        return {"csrf_token": csrf}

    @app.delete("/api/dev/session", status_code=204)
    def logout(request: Request, response: Response, x_csrf_token: str | None = Header(default=None)):
        actor(request, None, None, x_csrf_token, True)
        sessions.pop(request.cookies.get("hmw_session", ""), None)
        response.delete_cookie("hmw_session")

    @app.get("/api/personal/status")
    def get_status(request: Request, x_user_id: str | None = Header(default=None), x_principal_type: str | None = Header(default=None)):
        user = actor(request, x_user_id, x_principal_type, None)
        with Session(engine) as db:
            row = db.scalar(select(Status).where(Status.owner == user))
            return snapshot(row) if row else None

    @app.put("/api/personal/status")
    def save_status(payload: StatusInput, request: Request, x_user_id: str | None = Header(default=None), x_principal_type: str | None = Header(default=None), x_csrf_token: str | None = Header(default=None)):
        user = actor(request, x_user_id, x_principal_type, x_csrf_token, True)
        with Session(engine) as db, db.begin():
            row = db.scalar(select(Status).where(Status.owner == user).with_for_update())
            if row and payload.expected_version != row.version:
                raise HTTPException(409, "Version conflict")
            if not row:
                if payload.expected_version is not None:
                    raise HTTPException(409, "Version conflict")
                row = Status(id=str(uuid4()), owner=user, version=0, state="draft", body={}, updated_at=datetime.now(timezone.utc))
                db.add(row)
            row.version += 1
            row.state = "draft"
            row.body = payload.model_dump(exclude={"expected_version"})
            row.updated_at = datetime.now(timezone.utc)
            record(db, row)
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
            row.version += 1
            row.state = "published"
            row.updated_at = datetime.now(timezone.utc)
            record(db, row)
            return snapshot(row)

    return app


app = create_app()
