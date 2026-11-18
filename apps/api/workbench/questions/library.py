"""Project-scoped, read-only knowledge tools for a single question."""
from fastapi import HTTPException
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from workbench.knowledge.models import GuestPublication, KnowledgeVersion, SourceVersion, SourceModule
from workbench.knowledge.modules import PUBLIC_MODULE
from workbench.projects.models import Project
from workbench.questions.retrieval import _locator, _source_valid


class ProjectLibrary:
    PAGE_SIZE = 30
    READ_SIZE = 3000
    MAX_READS = 12

    def __init__(self, engine, owner_id, project_id, root, max_source_bytes):
        self.engine, self.owner_id, self.project_id = engine, owner_id, project_id
        self.root, self.max_source_bytes = root, max_source_bytes
        self.evidence = []
        self.listed_ids = set()
        with Session(engine) as db:
            if not db.scalar(select(Project.id).where(Project.id == project_id, Project.owner == owner_id)):
                raise HTTPException(404, 'Not found')

    def _query(self):
        shared = exists(select(GuestPublication.version_id).where(GuestPublication.version_id == KnowledgeVersion.id))
        return (select(KnowledgeVersion, SourceVersion, shared.label('guest_visible'))
                .join(SourceVersion, SourceVersion.id == KnowledgeVersion.source_id)
                .join(Project, Project.id == KnowledgeVersion.project_id)
                .where(Project.id == self.project_id, Project.owner == self.owner_id,
                       KnowledgeVersion.project_id == self.project_id,
                       SourceVersion.project_id == self.project_id, KnowledgeVersion.state == 'published'))

    def list_documents(self, offset=0):
        if type(offset) is not int or not 0 <= offset <= 100000:
            raise ValueError('invalid offset')
        with Session(self.engine) as db:
            rows = db.execute(select(KnowledgeVersion.id, SourceVersion.filename, func.length(KnowledgeVersion.content), SourceModule.module)
                              .join(SourceVersion, SourceVersion.id == KnowledgeVersion.source_id)
                              .outerjoin(SourceModule, SourceModule.source_id == SourceVersion.id)
                              .join(Project, Project.id == KnowledgeVersion.project_id)
                              .where(Project.id == self.project_id, Project.owner == self.owner_id,
                                     SourceVersion.project_id == self.project_id, KnowledgeVersion.state == 'published')
                              .order_by(KnowledgeVersion.id).offset(offset).limit(self.PAGE_SIZE + 1)).all()
            self.listed_ids.update(row[0] for row in rows[:self.PAGE_SIZE])
            return {'documents': [{'id': row[0], 'name': row[1], 'characters': row[2], 'module':row[3] or PUBLIC_MODULE} for row in rows[:self.PAGE_SIZE]],
                    'next_offset': offset + self.PAGE_SIZE if len(rows) > self.PAGE_SIZE else None}

    def history_is_current(self, sources):
        for knowledge_id, digest in sources:
            with Session(self.engine) as db:
                row = db.execute(self._query().where(KnowledgeVersion.id == knowledge_id)).first()
                if row is None or row[1].sha256 != digest:
                    return False
                source = row[1]
                if not _source_valid(self.root, source.object_key, digest, self.max_source_bytes):
                    return False
        return True

    def read_document(self, document_id, offset=0):
        if not isinstance(document_id, str) or len(document_id) > 64 or type(offset) is not int or not 0 <= offset <= 2147483647:
            raise ValueError('invalid request')
        if len(self.evidence) >= self.MAX_READS:
            raise ValueError('read limit reached')
        with Session(self.engine) as db:
            row = db.execute(self._query().where(KnowledgeVersion.id == document_id)).first()
            if row is None:
                raise ValueError('document unavailable')
            version, source, visible = row
            content, locator = version.content, version.locator
            source_id, filename, digest, key = source.id, source.filename, source.sha256, source.object_key
        if not _source_valid(self.root, key, digest, self.max_source_bytes):
            raise ValueError('source unavailable')
        with Session(self.engine) as db:
            current = db.execute(self._query().where(KnowledgeVersion.id == document_id)).first()
            if current is None or current[1].id != source_id or current[1].sha256 != digest or current[0].content != content:
                raise ValueError('document unavailable')
            visible = current[2]
        if offset >= len(content):
            raise ValueError('offset out of range')
        # A chunk may start inside a long line. Its locator still points to that line.
        end = min(len(content), offset + self.READ_SIZE)
        text = content[offset:end]
        line_no = len(content[:offset].splitlines()) + (0 if offset and content[offset-1] not in '\r\n' else 1)
        position = _locator(locator, max(1, line_no))
        if not position:
            raise ValueError('source locator unavailable')
        if 'start_line' in position:
            end_line_no = line_no + len(text.splitlines()) - (1 if text and not text.endswith(('\n', '\r')) else 0)
            end_position = _locator(locator, max(line_no, end_line_no))
            if end_position:
                position['end_line'] = end_position['end_line']
        item = {'knowledge_id':document_id, 'source_id':source_id, 'filename':filename,
                'source_sha256':digest, 'guest_visible':visible, 'locator':position,
                'excerpt':text, '_offset':offset}
        self.evidence.append(item)
        return {'number':len(self.evidence), 'text':text, 'next_offset':end if end < len(content) else None}

    def verify_catalog(self):
        if not self.listed_ids:
            return
        with Session(self.engine) as db:
            current = set(db.scalars(select(KnowledgeVersion.id)
                          .join(Project, Project.id == KnowledgeVersion.project_id)
                          .join(SourceVersion, SourceVersion.id == KnowledgeVersion.source_id)
                          .where(KnowledgeVersion.id.in_(self.listed_ids), Project.owner == self.owner_id,
                                 Project.id == self.project_id, SourceVersion.project_id == self.project_id,
                                 KnowledgeVersion.state == 'published')))
        if current != self.listed_ids:
            raise HTTPException(409, 'Knowledge availability changed; retry')

    def verified_evidence(self):
        result = []
        for item in self.evidence:
            with Session(self.engine) as db:
                row = db.execute(self._query().where(KnowledgeVersion.id == item['knowledge_id'])).first()
                if row is None:
                    raise HTTPException(409, 'Knowledge availability changed; retry')
                version, source, visible = row
                if (source.id != item['source_id'] or source.sha256 != item['source_sha256'] or
                    version.content[item['_offset']:item['_offset'] + len(item['excerpt'])] != item['excerpt'] or
                    not _source_valid(self.root, source.object_key, source.sha256, self.max_source_bytes)):
                    raise HTTPException(409, 'Knowledge availability changed; retry')
                result.append({**{k:v for k,v in item.items() if k != '_offset'}, 'guest_visible':visible})
        # Check all versions together after the potentially slow file checks.
        if self.evidence:
            with Session(self.engine) as db:
                current = {version.id: (source.id, source.sha256, visible)
                           for version, source, visible in db.execute(self._query().where(
                               KnowledgeVersion.id.in_([item['knowledge_id'] for item in self.evidence])))}
                for item in result:
                    status = current.get(item['knowledge_id'])
                    if status is None or status[:2] != (item['source_id'], item['source_sha256']):
                        raise HTTPException(409, 'Knowledge availability changed; retry')
                    item['guest_visible'] = status[2]
        return result
