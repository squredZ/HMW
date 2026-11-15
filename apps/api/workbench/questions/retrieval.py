"""Bounded, extractive retrieval over guest-visible published knowledge."""
import hashlib
import re
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from workbench.knowledge.models import GuestPublication, KnowledgeVersion, SourceVersion
from workbench.projects.models import Project

MAX_DOCUMENTS = 50
MAX_SEARCH_CHARS = 2_000_000
MAX_EVIDENCE = 3
_STOP_WORDS = {'什么','如何','哪些','是否','请问','哪个','当前','这个','怎么','多少','有哪','什么是','是什么'}

_LINE_BREAK = re.compile(r'\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]')


def _bounded_lines(content: str, budget: int):
    # Match str.splitlines(), which is also used when source locators are created.
    end = min(len(content), budget)
    start = 0
    for match in _LINE_BREAK.finditer(content, 0, end):
        yield content[start:match.start()], match.end() - start
        start = match.end()
    if start < end:
        yield content[start:end], end - start


def _terms(question: str) -> list[str]:
    found: set[str] = set()
    for token in re.findall(r'[a-z0-9_]{2,}|[\u4e00-\u9fff]{2,}', question.lower()):
        if re.fullmatch(r'[\u4e00-\u9fff]+', token):
            found.update(token[index:index + 2] for index in range(len(token) - 1))
            if len(token) <= 12:
                found.add(token)
        else:
            found.add(token)
    return sorted(found - _STOP_WORDS, key=lambda term: (-len(term), term))[:30]


def _source_valid(root: Path, key: str, expected: str, limit: int) -> bool:
    digest = hashlib.sha256()
    count = 0
    try:
        with (root / key).open('rb') as source:
            while chunk := source.read(1024 * 1024):
                count += len(chunk)
                if count > limit:
                    return False
                digest.update(chunk)
    except OSError:
        return False
    return digest.hexdigest() == expected


def _locator(locator: dict, line_no: int) -> dict | None:
    kind = locator.get('kind')
    if kind in ('docx', 'pptx'):
        anchor = next((a for a in locator.get('anchors', []) if a.get('line') == line_no), None)
        if not anchor:
            return None
        if kind == 'docx' and anchor.get('kind') in ('paragraph', 'table') and isinstance(anchor.get('index'), int) and anchor['index'] > 0:
            return {'kind':'docx', 'block_kind':anchor['kind'], 'index':anchor['index'], 'line':line_no}
        if kind == 'pptx' and isinstance(anchor.get('slide'), int) and isinstance(anchor.get('element'), int) and anchor['slide'] > 0 and anchor['element'] > 0:
            return {'kind':'pptx', 'slide':anchor['slide'], 'element':anchor['element'], 'line':line_no}
        return None
    start, end = locator.get('start_line'), locator.get('end_line')
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    original_line = start + line_no - 1
    return {'start_line':original_line, 'end_line':original_line} if original_line <= end else None


def guest_extract(engine: Engine, owner_id: str, project_id: str, question: str,
                  root: Path, max_source_bytes: int) -> dict:
    terms = _terms(question)
    evidence = []
    remaining = MAX_SEARCH_CHARS
    limited = False
    with Session(engine) as db:
        statement = (select(KnowledgeVersion, SourceVersion)
                     .join(GuestPublication, GuestPublication.version_id == KnowledgeVersion.id)
                     .join(SourceVersion, SourceVersion.id == KnowledgeVersion.source_id)
                     .join(Project, Project.id == KnowledgeVersion.project_id)
                     .where(Project.id == project_id, Project.owner == owner_id,
                            SourceVersion.project_id == project_id, KnowledgeVersion.state == 'published')
                     .order_by(KnowledgeVersion.id).limit(MAX_DOCUMENTS + 1))
        rows = db.execute(statement).yield_per(1)
        seen = False
        for index, (version, source) in enumerate(rows):
            seen = True
            if index >= MAX_DOCUMENTS or remaining <= 0:
                limited = True
                break
            if not terms:
                continue
            for line_no, (text, consumed) in enumerate(_bounded_lines(version.content, remaining), 1):
                scanned = text
                remaining -= consumed
                positions = [scanned.lower().find(term) for term in terms]
                positions = [position for position in positions if position >= 0]
                if positions:
                    locator = _locator(version.locator, line_no)
                    if locator:
                        position = min(positions)
                        excerpt = text[max(0, position - 150):position + 350]
                        evidence.append({'knowledge_id':version.id, 'source_id':source.id,
                                         'filename':source.filename, 'source_sha256':source.sha256,
                                         'locator':locator, 'excerpt':excerpt, '_object_key':source.object_key})
                    break
                if remaining <= 0:
                    limited = True
                    break
            if len(evidence) >= MAX_EVIDENCE:
                limited = True
                break
        if not seen:
            raise HTTPException(404, 'Not found')
    # Filesystem I/O occurs outside the database session; retain only metadata.
    checked = [(item, _source_valid(root, item['_object_key'], item['source_sha256'], max_source_bytes))
               for item in evidence]
    # Recheck current publication decisions after accessing source files, including
    # failed checks, so even the unavailable count cannot reveal revoked content.
    valid: set[str] = set()
    if checked:
        with Session(engine) as db:
            ids = [item['knowledge_id'] for item, _ in checked]
            valid = set(db.scalars(select(KnowledgeVersion.id)
                                   .join(GuestPublication, GuestPublication.version_id == KnowledgeVersion.id)
                                   .join(Project, Project.id == KnowledgeVersion.project_id)
                                   .where(KnowledgeVersion.id.in_(ids), KnowledgeVersion.project_id == project_id,
                                          KnowledgeVersion.state == 'published', Project.owner == owner_id)))
    unavailable = sum(1 for item, intact in checked if item['knowledge_id'] in valid and not intact)
    evidence = [item for item, intact in checked if item['knowledge_id'] in valid and intact]
    for item in evidence:
        item.pop('_object_key')
    return {'kind':'extractive' if evidence else 'insufficient_evidence',
            'message':'以下是可能相关的原文，请结合上下文判断。' if evidence else '未找到可核对的匹配依据。',
            'evidence':evidence, 'search_limited':limited, 'unavailable_sources':unavailable}
