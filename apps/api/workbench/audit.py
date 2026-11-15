"""Transactional metadata-only audit events for successful changes."""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from workbench.db import Base


class AuditEvent(Base):
    __tablename__ = 'audit_events'

    id: Mapped[str] = mapped_column(String, primary_key=True)
    actor_id: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    resource_id: Mapped[str] = mapped_column(String, nullable=False)
    resource_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_id: Mapped[str] = mapped_column(String, nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def record_audit(db: Session, *, actor_id: str, action: str, resource_id: str,
                 request_id: str, resource_version: int | None = None,
                 details: dict | None = None) -> None:
    """Add an event to the caller's transaction; never include content or secrets."""
    db.add(AuditEvent(id=str(uuid4()), actor_id=actor_id, action=action,
                      resource_id=resource_id, resource_version=resource_version,
                      request_id=request_id, details=details or {},
                      occurred_at=datetime.now(timezone.utc)))
