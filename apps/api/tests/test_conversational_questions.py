from fastapi.testclient import TestClient
from workbench.app import create_app
from test_guest_questions import submit


def test_questions_can_read_private_published_knowledge_but_not_drafts_or_retired(tmp_path):
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner':'password'}, storage_root=root)
    seen = []
    class FakeAgent:
        def answer(self, question, evidence, history=None):
            seen.extend(evidence)
            return {'answer':'版本是 V2 [1]。', 'citations':[1]}
    app.state.answer_agent = FakeAgent()
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf={'X-CSRF-Token':owner.post('/api/dev/session',json={'username':'owner','password':'password'}).json()['csrf_token']}
        project=owner.post('/api/projects',headers=csrf,json={'name':'项目'}).json()['id']
        private=submit(owner, app, root, project, csrf, '接口版本是 V2。', share=False)
        submit(owner, app, root, project, csrf, '接口草稿秘密。', share=None)
        retired=submit(owner, app, root, project, csrf, '接口停用秘密。', share=False)
        owner.post(f'/api/knowledge/{retired}/retire',headers=csrf)
        guest.post('/api/guest/session')
        assert guest.get('/api/guest/projects').json() == []
        assert guest.get('/api/guest/question-projects').json()[0]['id'] == project
        assert guest.get(f'/api/guest/knowledge/{private}').status_code == 404
        result=guest.post('/api/guest/questions',json={'project_id':project,'question':'接口'}).json()
        assert result['kind'] == 'generated'
        assert len(seen) == 1 and seen[0]['knowledge_id'] == private
        assert result['evidence'][0]['guest_visible'] is False
        assert '草稿秘密' not in str(seen) and '停用秘密' not in str(seen)
        source=owner.get(f'/api/knowledge/{private}').json()['source']['id']
        assert guest.get(f'/api/guest/sources/{source}/download').status_code == 404


def test_conversation_is_bounded_and_isolated_by_guest_and_conversation(tmp_path):
    root=tmp_path / 'objects'
    app=create_app(f"sqlite:///{tmp_path / 'db.sqlite'}",dev_users={'owner':'password'},storage_root=root)
    histories=[]
    class FakeAgent:
        def answer(self, question, evidence, history=None):
            histories.append(history or [])
            return {'answer':'可以参考资料 [1]。','citations':[1]}
    app.state.answer_agent=FakeAgent()
    with TestClient(app) as owner, TestClient(app) as first, TestClient(app) as second:
        csrf={'X-CSRF-Token':owner.post('/api/dev/session',json={'username':'owner','password':'password'}).json()['csrf_token']}
        project=owner.post('/api/projects',headers=csrf,json={'name':'项目'}).json()['id']
        submit(owner,app,root,project,csrf,'接口版本是 V2。',share=True)
        first.post('/api/guest/session'); second.post('/api/guest/session')
        payload={'project_id':project,'question':'接口版本','conversation_id':'d61d38a4-2512-43b0-9fd8-e1d10c9b272b'}
        assert first.post('/api/guest/questions',json=payload).status_code == 200
        assert first.post('/api/guest/questions',json={**payload,'question':'它有哪些特点'}).status_code == 200
        assert histories[1] == [{'question':'接口版本','answer':'可以参考资料 [1]。'}]
        assert second.post('/api/guest/questions',json=payload).status_code == 200
        assert histories[2] == []
        assert first.post('/api/guest/questions',json={**payload,'conversation_id':'b436c205-6b68-432f-80b1-c91478a0d500'}).status_code == 200
        assert histories[3] == []


def test_retrieval_can_find_matching_document_after_document_budget(tmp_path, monkeypatch):
    from workbench.questions import retrieval
    root=tmp_path / 'objects'
    app=create_app(f"sqlite:///{tmp_path / 'db.sqlite'}",dev_users={'owner':'password'},storage_root=root)
    class FakeAgent:
        def answer(self, question, evidence, history=None):
            assert any('无缝迁移' in item['excerpt'] for item in evidence)
            return {'answer':'支持无缝迁移 [1]。','citations':[1]}
    app.state.answer_agent=FakeAgent()
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf={'X-CSRF-Token':owner.post('/api/dev/session',json={'username':'owner','password':'password'}).json()['csrf_token']}
        project=owner.post('/api/projects',headers=csrf,json={'name':'项目'}).json()['id']
        for _ in range(10):
            submit(owner,app,root,project,csrf,'不相关内容。',share=False)
        submit(owner,app,root,project,csrf,'系统支持无缝迁移。',share=False)
        monkeypatch.setattr(retrieval, 'MAX_DOCUMENTS', 1)
        guest.post('/api/guest/session')
        result=guest.post('/api/guest/questions',json={'project_id':project,'question':'无缝迁移'}).json()
        assert result['kind'] == 'generated'
        assert any('无缝迁移' in item['excerpt'] for item in result['evidence'])


def test_retired_knowledge_cannot_be_returned_when_retired_during_generation(tmp_path):
    root=tmp_path / 'objects'
    app=create_app(f"sqlite:///{tmp_path / 'db.sqlite'}",dev_users={'owner':'password'},storage_root=root)
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf={'X-CSRF-Token':owner.post('/api/dev/session',json={'username':'owner','password':'password'}).json()['csrf_token']}
        project=owner.post('/api/projects',headers=csrf,json={'name':'项目'}).json()['id']
        version=submit(owner,app,root,project,csrf,'接口版本是 V2。',share=False)
        class RetiringAgent:
            def answer(self, question, evidence):
                assert owner.post(f'/api/knowledge/{version}/retire',headers=csrf).status_code == 200
                return {'answer':'版本是 V2 [1]。','citations':[1]}
        app.state.answer_agent=RetiringAgent()
        guest.post('/api/guest/session')
        response=guest.post('/api/guest/questions',json={'project_id':project,'question':'接口'})
        assert response.status_code == 409 and 'V2' not in response.text


def test_followup_does_not_feed_answer_from_retired_source_back_to_model(tmp_path):
    root=tmp_path / 'objects'
    app=create_app(f"sqlite:///{tmp_path / 'db.sqlite'}",dev_users={'owner':'password'},storage_root=root)
    histories=[]
    class FakeAgent:
        def answer(self, question, evidence, history=None):
            histories.append(history or [])
            return {'answer':'旧版本细节 [1]。','citations':[1]}
    app.state.answer_agent=FakeAgent()
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf={'X-CSRF-Token':owner.post('/api/dev/session',json={'username':'owner','password':'password'}).json()['csrf_token']}
        project=owner.post('/api/projects',headers=csrf,json={'name':'项目'}).json()['id']
        version=submit(owner,app,root,project,csrf,'接口包含旧版本细节。',share=False)
        guest.post('/api/guest/session')
        payload={'project_id':project,'question':'接口','conversation_id':'d61d38a4-2512-43b0-9fd8-e1d10c9b272b'}
        assert guest.post('/api/guest/questions',json=payload).status_code == 200
        owner.post(f'/api/knowledge/{version}/retire',headers=csrf)
        submit(owner,app,root,project,csrf,'接口新版信息。',share=False)
        assert guest.post('/api/guest/questions',json={**payload,'question':'有哪些变化'}).status_code == 200
        assert histories[1][0]['question'] == '接口'
        assert histories[1][0]['answer'] == ''


def test_followup_prioritizes_current_question_over_previous_keywords(tmp_path, monkeypatch):
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from workbench.knowledge.models import KnowledgeVersion
    from workbench.questions import retrieval

    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner': 'password'}, storage_root=root)
    captured = []

    class FakeAgent:
        def answer(self, question, evidence, history=None):
            captured.append(evidence)
            return {'answer': '请参考资料 [1]。', 'citations': [1]}

    app.state.answer_agent = FakeAgent()
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf = {'X-CSRF-Token': owner.post('/api/dev/session', json={'username':'owner','password':'password'}).json()['csrf_token']}
        project = owner.post('/api/projects', headers=csrf, json={'name':'项目'}).json()['id']
        ids = [submit(owner, app, root, project, csrf, '接口说明和部署步骤。', share=False) for _ in range(4)]
        with Session(app.state.engine) as db:
            for version in db.scalars(select(KnowledgeVersion).where(KnowledgeVersion.id.in_(ids))):
                version.content = '部署步骤：先安装。' if version.id == max(ids) else '接口说明。'
            db.commit()
        monkeypatch.setattr(retrieval, 'MAX_EVIDENCE', 1)
        guest.post('/api/guest/session')
        payload = {'project_id': project, 'question': '接口', 'conversation_id': 'd61d38a4-2512-43b0-9fd8-e1d10c9b272b'}
        assert guest.post('/api/guest/questions', json=payload).status_code == 200
        assert guest.post('/api/guest/questions', json={**payload, 'question': '部署步骤'}).status_code == 200
        assert captured[-1][0]['knowledge_id'] == max(ids)
