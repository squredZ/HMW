"""Issues submitted by temporary visitors for owner review."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from workbench.db import Base


class Issue(Base):
    __tablename__ = 'issues'

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    visitor_id: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IssueSubmission(Base):
    """Bind one visitor's retry key to the committed issue and request digest."""
    __tablename__ = 'issue_submissions'

    visitor_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    key_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    issue_id: Mapped[str] = mapped_column(ForeignKey('issues.id'), nullable=False)


class IssueDetail(Base):
    """The reported module is independent of the optional log attachment."""
    __tablename__ = 'issue_details'

    issue_id: Mapped[str] = mapped_column(ForeignKey('issues.id'), primary_key=True)
    module: Mapped[str] = mapped_column(String(200), nullable=False)
