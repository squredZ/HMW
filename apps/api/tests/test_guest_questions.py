from fastapi.testclient import TestClient
from workbench.app import create_app
from workbench.jobs.worker import process_one


def submit(owner, app, root, project, csrf, text, *, share):
    response = owner.post(f'/api/projects/{project}/imports', headers=csrf,
                          files=[('files',('source.md',text.encode(),'text/markdown'))])
    job = response.json()['jobs'][0]
    assert process_one(app.state.engine, root, 'worker')
    version = owner.get(f'/api/imports/{job}').json()['result']['knowledge_id']
    if share is not None:
        assert owner.post('/api/knowledge/publish-batch', headers=csrf,
                          json={'version_ids':[version], 'guest_visible':share}).status_code == 200
    return version


def test_guest_questions_use_only_shared_published_sources(tmp_path):
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner':'password'}, storage_root=root)
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf = {'X-CSRF-Token':owner.post('/api/dev/session',json={'username':'owner','password':'password'}).json()['csrf_token']}
        pid = owner.post('/api/projects',headers=csrf,json={'name':'A'}).json()['id']
        shared = submit(owner, app, root, pid, csrf, '# 规范\n接口版本为 V2。\n', share=True)
        submit(owner, app, root, pid, csrf, '秘密口令仅用于测试。', share=False)
        submit(owner, app, root, pid, csrf, '尚未发布的燃料算法。', share=None)
        assert guest.post('/api/guest/questions',json={'project_id':pid,'question':'接口版本是什么？'}).status_code == 401
        guest.post('/api/guest/session')
        result = guest.post('/api/guest/questions',json={'project_id':pid,'question':'接口版本是什么？'})
        assert result.status_code == 200
        data = result.json()
        assert data['kind'] == 'extractive'
        assert len(data['evidence']) == 1
        assert data['evidence'][0]['knowledge_id'] == shared
        assert data['evidence'][0]['excerpt'] == '接口版本为 V2。'
        assert data['evidence'][0]['locator'] == {'start_line':2,'end_line':2}
        assert guest.post('/api/guest/questions',json={'project_id':pid,'question':'秘密口令'}).json()['evidence'] == []
        assert guest.post('/api/guest/questions',json={'project_id':pid,'question':'燃料算法'}).json()['evidence'] == []
        owner.put(f'/api/knowledge/{shared}/guest-visibility',headers=csrf,json={'visible':False})
        assert guest.post('/api/guest/questions',json={'project_id':pid,'question':'接口版本'}).status_code == 404


def test_damaged_source_is_not_used_as_answer_evidence(tmp_path):
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner':'password'}, storage_root=root)
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf = {'X-CSRF-Token':owner.post('/api/dev/session',json={'username':'owner','password':'password'}).json()['csrf_token']}
        pid = owner.post('/api/projects',headers=csrf,json={'name':'A'}).json()['id']
        submit(owner, app, root, pid, csrf, '接口版本为 V2。', share=True)
        next(root.iterdir()).write_bytes(b'tampered')
        guest.post('/api/guest/session')
        result = guest.post('/api/guest/questions',json={'project_id':pid,'question':'接口版本'}).json()
        assert result['evidence'] == []
        assert result['kind'] == 'insufficient_evidence'
        assert result['unavailable_sources'] == 1


def test_search_budget_reports_incomplete_when_many_empty_lines_are_skipped(tmp_path, monkeypatch):
    from workbench.questions import retrieval
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner':'password'}, storage_root=root)
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf = {'X-CSRF-Token':owner.post('/api/dev/session',json={'username':'owner','password':'password'}).json()['csrf_token']}
        pid = owner.post('/api/projects',headers=csrf,json={'name':'A'}).json()['id']
        submit(owner, app, root, pid, csrf, '\n' * 100 + '接口版本是 V2。', share=True)
        guest.post('/api/guest/session')
        monkeypatch.setattr(retrieval, 'MAX_SEARCH_CHARS', 10)
        data = guest.post('/api/guest/questions',json={'project_id':pid,'question':'接口版本'}).json()
        assert data['evidence'] == []
        assert data['search_limited'] is True


def test_citations_match_source_lines_with_carriage_return_endings(tmp_path):
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner':'password'}, storage_root=root)
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf = {'X-CSRF-Token':owner.post('/api/dev/session',json={'username':'owner','password':'password'}).json()['csrf_token']}
        pid = owner.post('/api/projects',headers=csrf,json={'name':'A'}).json()['id']
        submit(owner, app, root, pid, csrf, '引言\r接口版本为V2\r结束', share=True)
        guest.post('/api/guest/session')
        data = guest.post('/api/guest/questions',json={'project_id':pid,'question':'接口版本'}).json()
        assert data['evidence'][0]['excerpt'] == '接口版本为V2'
        assert data['evidence'][0]['locator'] == {'start_line':2,'end_line':2}


def test_rechecks_visibility_after_source_validation(tmp_path, monkeypatch):
    from workbench.questions import retrieval
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner':'password'}, storage_root=root)
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf = {'X-CSRF-Token':owner.post('/api/dev/session',json={'username':'owner','password':'password'}).json()['csrf_token']}
        pid = owner.post('/api/projects',headers=csrf,json={'name':'A'}).json()['id']
        version = submit(owner, app, root, pid, csrf, '接口版本为 V2。', share=True)
        guest.post('/api/guest/session')
        original = retrieval._source_valid
        def revoke_while_checking(*args):
            valid = original(*args)
            assert owner.put(f'/api/knowledge/{version}/guest-visibility',headers=csrf,json={'visible':False}).status_code == 200
            return valid
        monkeypatch.setattr(retrieval, '_source_valid', revoke_while_checking)
        data = guest.post('/api/guest/questions',json={'project_id':pid,'question':'接口版本'}).json()
        assert data['evidence'] == []
        assert data['kind'] == 'insufficient_evidence'
        assert data['unavailable_sources'] == 0


def test_word_evidence_retains_document_and_paragraph_location(tmp_path):
    from io import BytesIO
    from docx import Document
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner':'password'}, storage_root=root)
    doc = Document()
    doc.add_paragraph('接口版本为 V2。')
    source = BytesIO()
    doc.save(source)
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf = {'X-CSRF-Token':owner.post('/api/dev/session',json={'username':'owner','password':'password'}).json()['csrf_token']}
        pid = owner.post('/api/projects',headers=csrf,json={'name':'A'}).json()['id']
        job = owner.post(f'/api/projects/{pid}/imports',headers=csrf,files=[('files',('word.docx',source.getvalue(),'application/octet-stream'))]).json()['jobs'][0]
        process_one(app.state.engine, root, 'worker')
        version = owner.get(f'/api/imports/{job}').json()['result']['knowledge_id']
        owner.post('/api/knowledge/publish-batch',headers=csrf,json={'version_ids':[version],'guest_visible':True})
        guest.post('/api/guest/session')
        evidence = guest.post('/api/guest/questions',json={'project_id':pid,'question':'接口版本'}).json()['evidence'][0]
        assert evidence['locator'] == {'kind':'docx','block_kind':'paragraph','index':1,'line':1}


def test_question_requires_project_and_rejects_unimplemented_baseline(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner':'password'})
    with TestClient(app) as guest:
        guest.post('/api/guest/session')
        assert guest.post('/api/guest/questions',json={'question':'接口版本'}).status_code == 422
        assert guest.post('/api/guest/questions',json={'project_id':'project','question':'接口版本','baseline_id':'unknown'}).status_code == 422


def test_question_log_has_request_id_without_question_or_source_text(tmp_path, caplog):
    import json
    import logging
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner':'password'}, storage_root=root)
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf = {'X-CSRF-Token':owner.post('/api/dev/session',json={'username':'owner','password':'password'}).json()['csrf_token']}
        pid = owner.post('/api/projects',headers=csrf,json={'name':'A'}).json()['id']
        submit(owner, app, root, pid, csrf, '接口版本为 V2。', share=True)
        guest.post('/api/guest/session')
        with caplog.at_level(logging.INFO, logger='workbench.questions'):
            response = guest.post('/api/guest/questions',json={'project_id':pid,'question':'接口版本'})
        events = [json.loads(record.message) for record in caplog.records if record.name == 'workbench.questions']
        assert events[0]['request_id'] == response.headers['X-Request-ID']
        assert events[0]['project_id'] == pid
        assert events[0]['evidence_count'] == 1
        assert '接口版本' not in caplog.text
        assert 'V2' not in caplog.text
