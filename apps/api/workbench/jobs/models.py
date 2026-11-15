"""Database-backed work items and their monotonically increasing fencing tokens."""
from sqlalchemy import JSON, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from workbench.db import Base


class Job(Base):
    __tablename__ = 'jobs'

    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False, index=True)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    worker_id: Mapped[str | None] = mapped_column(String, nullable=True)
    expires_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
