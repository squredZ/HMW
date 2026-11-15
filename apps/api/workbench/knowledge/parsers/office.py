"""Extract Office text with stable anchors without executing document content."""
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZipFile

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml import etree
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE


class ParseBlocked(ValueError):
    """An input needs explicit user action before it can be imported."""


@dataclass(frozen=True)
class ParsedOffice:
    content: str
    locator: dict


def _inspect_archive(data: bytes, *, max_expanded_bytes: int) -> tuple[set[str], bool]:
    try:
        with ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 10000 or sum(entry.file_size for entry in entries) > max_expanded_bytes:
                raise ParseBlocked('expansion_limit')
            names: set[str] = set()
            revisions = False
            parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
            revision_tags = {qn('w:ins'), qn('w:del'), qn('w:moveFrom'), qn('w:moveTo')}
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if entry.flag_bits & 1:
                    raise ParseBlocked('encrypted_file')
                if path.is_absolute() or '..' in path.parts or entry.filename in names:
                    raise ParseBlocked('invalid_office_file')
                names.add(entry.filename)
                if entry.filename.lower().endswith('vbaproject.bin'):
                    raise ParseBlocked('macros_not_supported')
                if entry.filename.endswith('.xml'):
                    xml = archive.read(entry)
                    if b'<!DOCTYPE' in xml.upper():
                        raise ParseBlocked('unsafe_xml')
                    if entry.filename.startswith('word/'):
                        root = etree.fromstring(xml, parser)
                        revisions = revisions or any(element.tag in revision_tags for element in root.iter())
            return names, revisions
    except ParseBlocked:
        raise
    except (BadZipFile, RuntimeError, KeyError, ValueError, etree.XMLSyntaxError):
        raise ParseBlocked('invalid_office_file') from None


def _word(data: bytes, names: set[str], revisions: bool) -> ParsedOffice:
    if revisions:
        raise ParseBlocked('unresolved_revisions')
    document = Document(BytesIO(data))
    lines: list[str] = []
    anchors: list[dict] = []
    counts = {'paragraph':0, 'table':0}
    for block in document.iter_inner_content():
        kind = 'paragraph' if isinstance(block, Paragraph) else 'table'
        counts[kind] += 1
        values = [block.text] if isinstance(block, Paragraph) else [
            ' | '.join(cell.text for cell in row.cells) for row in block.rows
        ] if isinstance(block, Table) else []
        for value in values:
            for line in value.splitlines() or ['']:
                lines.append(line)
                anchors.append({'line':len(lines), 'kind':kind, 'index':counts[kind]})
    warnings = ['comments_present'] if 'word/comments.xml' in names else []
    return ParsedOffice('\n'.join(lines), {'kind':'docx', 'anchors':anchors, 'warnings':warnings})


def _slides(data: bytes, names: set[str]) -> ParsedOffice:
    presentation = Presentation(BytesIO(data))
    lines: list[str] = []
    anchors: list[dict] = []
    has_images = any(name.startswith('ppt/media/') for name in names)
    for slide_no, slide in enumerate(presentation.slides, 1):
        for shape_no, shape in enumerate(slide.shapes, 1):
            has_images = has_images or shape.shape_type == MSO_SHAPE_TYPE.PICTURE
            values = []
            if shape.has_text_frame:
                values.append(shape.text_frame.text)
            if shape.has_table:
                values.extend(' | '.join(cell.text for cell in row.cells) for row in shape.table.rows)
            for value in values:
                for line in value.splitlines() or ['']:
                    lines.append(line)
                    anchors.append({'line':len(lines), 'slide':slide_no, 'element':shape_no})
    warnings = ['images_require_review'] if has_images else []
    return ParsedOffice('\n'.join(lines), {'kind':'pptx', 'anchors':anchors, 'warnings':warnings})


def parse_office(data: bytes, filename: str, *, max_expanded_bytes: int = 250 * 1024 * 1024) -> ParsedOffice:
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix in ('.doc', '.ppt'):
        raise ParseBlocked('converter_unavailable')
    if suffix not in ('.docx', '.pptx') or max_expanded_bytes <= 0:
        raise ParseBlocked('invalid_office_file')
    # Encrypted OOXML is commonly wrapped in a compound binary container.
    if data.startswith(bytes.fromhex('D0CF11E0A1B11AE1')):
        raise ParseBlocked('encrypted_or_unsupported')
    names, revisions = _inspect_archive(data, max_expanded_bytes=max_expanded_bytes)
    try:
        result = _word(data, names, revisions) if suffix == '.docx' else _slides(data, names)
    except ParseBlocked:
        raise
    except (KeyError, ValueError, TypeError, BadZipFile, etree.XMLSyntaxError):
        raise ParseBlocked('invalid_office_file') from None
    if not result.content.strip():
        raise ParseBlocked('no_extractable_text')
    return result
