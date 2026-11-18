from sqlalchemy import JSON, Boolean, ForeignKey, String, Text
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


class SourceModule(Base):
    __tablename__ = 'source_modules'
    source_id: Mapped[str] = mapped_column(ForeignKey('source_versions.id'), primary_key=True)
    module: Mapped[str] = mapped_column(String(200), nullable=False)


class ProjectSkill(Base):
    __tablename__ = 'project_skills'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    module: Mapped[str] = mapped_column(String(200), nullable=False)
    module_key: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
