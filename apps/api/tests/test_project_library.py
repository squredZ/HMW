from fastapi.testclient import TestClient
from workbench.app import create_app
from test_guest_questions import submit


def test_agent_can_explore_project_without_keyword_match_and_cannot_access_other_projects(tmp_path):
    root = tmp_path / 'objects'
    app = create_app(f'sqlite:///{tmp_path / "db.sqlite"}', dev_users={'owner':'password'}, storage_root=root)
    class Explorer:
        def explore(self, question, history, library):
            docs = library.list_documents()['documents']
            assert len(docs) == 1
            assert library.read_document(docs[0]['id'])['text'] == '产品功能：可靠的数据同步。'
            return {'answer':'它可以同步数据 [1]。','citations':[1]}
    app.state.answer_agent = Explorer()
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf = {'X-CSRF-Token':owner.post('/api/dev/session', json={'username':'owner','password':'password'}).json()['csrf_token']}
        project = owner.post('/api/projects',headers=csrf,json={'name':'项目'}).json()['id']
        other = owner.post('/api/projects',headers=csrf,json={'name':'其他'}).json()['id']
        submit(owner,app,root,project,csrf,'产品功能：可靠的数据同步。',share=False)
        submit(owner,app,root,project,csrf,'草稿不应暴露',share=None)
        submit(owner,app,root,other,csrf,'其他项目',share=False)
        guest.post('/api/guest/session')
        result = guest.post('/api/guest/questions',json={'project_id':project,'question':'它有什么用？'}).json()
        assert result['kind'] == 'generated' and result['citations'] == [1]
        assert result['evidence'][0]['guest_visible'] is False


def test_library_rechecks_retired_knowledge_after_agent_read(tmp_path):
    root = tmp_path / 'objects'
    app = create_app(f'sqlite:///{tmp_path / "db.sqlite"}',dev_users={'owner':'password'},storage_root=root)
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf={'X-CSRF-Token':owner.post('/api/dev/session',json={'username':'owner','password':'password'}).json()['csrf_token']}
        project=owner.post('/api/projects',headers=csrf,json={'name':'项目'}).json()['id']
        version=submit(owner,app,root,project,csrf,'项目功能。',share=False)
        class Explorer:
            def explore(self,question,history,library):
                library.read_document(version)
                owner.post(f'/api/knowledge/{version}/retire',headers=csrf)
                return {'answer':'项目功能 [1]','citations':[1]}
        app.state.answer_agent=Explorer()
        guest.post('/api/guest/session')
        assert guest.post('/api/guest/questions',json={'project_id':project,'question':'介绍一下'}).status_code == 409


def test_catalog_pages_and_long_documents_can_be_read_independently(tmp_path, monkeypatch):
    from workbench.questions.library import ProjectLibrary
    root = tmp_path / 'objects'
    app = create_app(f'sqlite:///{tmp_path / "db.sqlite"}', dev_users={'owner':'password'}, storage_root=root)
    with TestClient(app) as owner:
        csrf = {'X-CSRF-Token':owner.post('/api/dev/session',json={'username':'owner','password':'password'}).json()['csrf_token']}
        project = owner.post('/api/projects',headers=csrf,json={'name':'项目'}).json()['id']
        submit(owner,app,root,project,csrf,'甲' * 3500,share=False)
        submit(owner,app,root,project,csrf,'乙' * 3500,share=False)
        monkeypatch.setattr(ProjectLibrary, 'PAGE_SIZE', 1)
        library = ProjectLibrary(app.state.engine,'owner',project,root,50 * 1024 * 1024)
        first = library.list_documents()
        second = library.list_documents(first['next_offset'])
        assert first['documents'][0]['id'] != second['documents'][0]['id']
        part = library.read_document(second['documents'][0]['id'])
        assert len(part['text']) == 3000 and part['next_offset'] == 3000
        assert len(library.read_document(second['documents'][0]['id'], part['next_offset'])['text']) == 500
        library.verify_catalog()
        assert len(library.verified_evidence()) == 2


def test_retirement_during_file_verification_prevents_tool_read(tmp_path, monkeypatch):
    import pytest
    from workbench.questions import library as library_module
    root = tmp_path / 'objects'
    app = create_app(f'sqlite:///{tmp_path / "db.sqlite"}',dev_users={'owner':'password'},storage_root=root)
    with TestClient(app) as owner:
        csrf={'X-CSRF-Token':owner.post('/api/dev/session',json={'username':'owner','password':'password'}).json()['csrf_token']}
        project=owner.post('/api/projects',headers=csrf,json={'name':'项目'}).json()['id']
        version=submit(owner,app,root,project,csrf,'已发布资料。',share=False)
        original=library_module._source_valid
        def retire_during_check(*args):
            valid=original(*args)
            owner.post(f'/api/knowledge/{version}/retire',headers=csrf)
            return valid
        monkeypatch.setattr(library_module,'_source_valid',retire_during_check)
        library=library_module.ProjectLibrary(app.state.engine,'owner',project,root,50 * 1024 * 1024)
        with pytest.raises(ValueError,match='document unavailable'):
            library.read_document(version)
        assert library.evidence == []


def test_agent_can_reply_conversationally_without_claiming_a_citation(tmp_path):
    root=tmp_path / 'objects'
    app=create_app(f'sqlite:///{tmp_path / "db.sqlite"}',dev_users={'owner':'password'},storage_root=root)
    class Explorer:
        def explore(self,question,history,library):
            return {'answer':'你好，你想了解项目的哪个方面？','citations':[]}
    app.state.answer_agent=Explorer()
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf={'X-CSRF-Token':owner.post('/api/dev/session',json={'username':'owner','password':'password'}).json()['csrf_token']}
        project=owner.post('/api/projects',headers=csrf,json={'name':'项目'}).json()['id']
        guest.post('/api/guest/session')
        result=guest.post('/api/guest/questions',json={'project_id':project,'question':'你好'}).json()
        assert result['kind'] == 'generated' and result['ungrounded'] is True
        assert result['message'] == '你好，你想了解项目的哪个方面？'
