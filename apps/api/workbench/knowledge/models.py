from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from workbench.db import Base


class SourceVersion(Base):
    __tablename__ = 'source_versions'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    object_key: Mapped[str] = mapped_column(String, nullable=False)
    sha256: Mapped[str] = mapped_column(String, nullable=False)


class KnowledgeVersion(Base):
    __tablename__ = 'knowledge_versions'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_id: Mapped[str] = mapped_column(String, nullable=False)
    project_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    locator: Mapped[dict] = mapped_column(JSON, nullable=False)


class GuestPublication(Base):
    """An explicit owner decision to share one published version with guests."""
    __tablename__ = 'guest_publications'
    version_id: Mapped[str] = mapped_column(String, primary_key=True)
