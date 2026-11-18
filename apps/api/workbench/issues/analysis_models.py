"""Persistent issue attachments and manually requested analysis runs."""
from sqlalchemy import Float, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from workbench.db import Base


class IssueAttachment(Base):
    __tablename__ = 'issue_attachments'

    issue_id: Mapped[str] = mapped_column(ForeignKey('issues.id'), primary_key=True)
    module: Mapped[str] = mapped_column(String(200), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    object_key: Mapped[str] = mapped_column(String(64), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class IssueAnalysis(Base):
    __tablename__ = 'issue_analyses'
    __table_args__ = (Index('one_active_analysis_per_issue', 'issue_id', unique=True,
                            sqlite_where=text("state IN ('queued', 'running')"),
                            postgresql_where=text("state IN ('queued', 'running')")),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    issue_id: Mapped[str] = mapped_column(ForeignKey('issues.id'), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    report: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
