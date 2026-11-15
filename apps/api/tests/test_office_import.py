from io import BytesIO
from fastapi.testclient import TestClient
from docx import Document
from pptx import Presentation
from pptx.util import Inches

from workbench.app import create_app
from workbench.jobs.worker import process_one


def headers(user):
    return {'X-User-ID':user,'X-Principal-Type':'human_session','X-CSRF-Token':'test'}


def office_file(kind):
    out = BytesIO()
    if kind == 'docx':
        doc = Document()
        doc.add_paragraph('工程约束')
        doc.save(out)
    else:
        doc = Presentation()
        slide = doc.slides.add_slide(doc.slide_layouts[6])
        slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)).text_frame.text = '设计概览'
        doc.save(out)
    return out.getvalue()


def test_office_files_flow_through_worker_with_source_anchors(tmp_path):
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", testing=True, storage_root=root)
    with TestClient(app) as client:
        pid = client.post('/api/projects', headers=headers('owner'), json={'name':'A'}).json()['id']
        for ext, text in [('docx','工程约束'),('pptx','设计概览')]:
            raw = office_file(ext)
            response = client.post(f'/api/projects/{pid}/imports', headers=headers('owner'), files=[('files',(f'doc.{ext}',raw,'application/octet-stream'))])
            assert response.status_code == 202
            job_id = response.json()['jobs'][0]
            assert process_one(app.state.engine, root, 'worker')
            job = client.get(f'/api/imports/{job_id}', headers=headers('owner')).json()
            assert job['state'] == 'completed'
            draft = client.get(f"/api/knowledge/{job['result']['knowledge_id']}", headers=headers('owner')).json()
            assert text in draft['content']
            assert draft['locator']['kind'] == ext
            assert draft['locator']['anchors']
            assert client.get(f"/api/sources/{draft['source']['id']}/download",headers=headers('owner')).content == raw


def test_legacy_file_returns_converter_unavailable(tmp_path):
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", testing=True, storage_root=root)
    with TestClient(app) as client:
        pid = client.post('/api/projects', headers=headers('owner'), json={'name':'A'}).json()['id']
        response = client.post(f'/api/projects/{pid}/imports', headers=headers('owner'), files=[('files',('old.doc',b'legacy bytes','application/octet-stream'))])
        assert response.status_code == 202
        job = response.json()['jobs'][0]
        process_one(app.state.engine, root, 'worker')
        result = client.get(f'/api/imports/{job}', headers=headers('owner')).json()
        assert result['state'] == 'needs_input'
        assert result['result']['reason'] == 'converter_unavailable'
