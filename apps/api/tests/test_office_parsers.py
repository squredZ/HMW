from io import BytesIO
import zipfile

import pytest
from docx import Document
from pptx import Presentation
from pptx.util import Inches

from workbench.knowledge.parsers.office import ParseBlocked, parse_office


def word_bytes():
    doc = Document()
    doc.add_heading('设计说明', level=1)
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = '接口'
    table.cell(0, 1).text = '版本一'
    data = BytesIO()
    doc.save(data)
    return data.getvalue()


def slides_bytes():
    slides = Presentation()
    slide = slides.slides.add_slide(slides.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
    box.text_frame.text = '架构概览'
    slide.notes_slide.notes_text_frame.text = '机密备注'
    data = BytesIO()
    slides.save(data)
    return data.getvalue()


def test_word_extracts_table_and_stable_paragraph_anchors():
    result = parse_office(word_bytes(), 'document.docx')
    assert '设计说明' in result.content and '接口' in result.content and '版本一' in result.content
    assert any(anchor['kind'] == 'paragraph' for anchor in result.locator['anchors'])
    assert any(anchor['kind'] == 'table' for anchor in result.locator['anchors'])


def test_slides_use_slide_number_and_exclude_notes_by_default():
    result = parse_office(slides_bytes(), 'slides.pptx')
    assert '架构概览' in result.content
    assert '机密备注' not in result.content
    assert result.locator['anchors'][0]['slide'] == 1


def test_expansion_limit_is_enforced_before_parsing():
    with pytest.raises(ParseBlocked, match='expansion_limit'):
        parse_office(word_bytes(), 'document.docx', max_expanded_bytes=100)


def test_unresolved_word_revisions_block_import():
    source = BytesIO(word_bytes())
    result = BytesIO()
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(result, 'w') as rebuilt:
        for item in original.infolist():
            content = original.read(item.filename)
            if item.filename == 'word/document.xml':
                content = content.replace(b'</w:body>', b'<w:ins w:id="0"><w:r><w:t>changed</w:t></w:r></w:ins></w:body>')
            rebuilt.writestr(item, content)
    with pytest.raises(ParseBlocked, match='unresolved_revisions'):
        parse_office(result.getvalue(), 'document.docx')


def test_invalid_and_legacy_files_have_actionable_failures():
    with pytest.raises(ParseBlocked, match='invalid_office_file'):
        parse_office(b'not-a-zip', 'broken.docx')
    with pytest.raises(ParseBlocked, match='converter_unavailable'):
        parse_office(b'legacy', 'old.doc')


def test_valid_zip_without_office_parts_is_reported_as_invalid():
    data = BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('unrelated.txt', 'hello')
    with pytest.raises(ParseBlocked, match='invalid_office_file'):
        parse_office(data.getvalue(), 'bad.docx')


def test_slides_with_picture_require_manual_review():
    import base64
    slides = Presentation()
    slide = slides.slides.add_slide(slides.slide_layouts[6])
    slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)).text_frame.text = '图示'
    image = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jR1cAAAAASUVORK5CYII=')
    slide.shapes.add_picture(BytesIO(image), Inches(1), Inches(2))
    data = BytesIO()
    slides.save(data)
    result = parse_office(data.getvalue(), 'picture.pptx')
    assert 'images_require_review' in result.locator['warnings']


def test_encrypted_ooxml_container_requests_readable_copy():
    with pytest.raises(ParseBlocked, match='encrypted_or_unsupported'):
        parse_office(bytes.fromhex('D0CF11E0A1B11AE1') + b'opaque', 'protected.docx')
