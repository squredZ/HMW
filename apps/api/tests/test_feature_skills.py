import logging
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from workbench.app import create_app
from workbench.jobs.worker import process_one


def headers(user='owner'):
    return {'X-User-ID':user, 'X-Principal-Type':'human_session', 'X-CSRF-Token':'test'}


def test_import_module_defaults_and_selected_module_survive_worker(tmp_path):
    root = tmp_path / 'objects'
    app = create_app(f'sqlite:///{tmp_path / "db.sqlite"}', testing=True, storage_root=root)
    with TestClient(app) as client:
        project = client.post('/api/projects', headers=headers(), json={'name':'项目'}).json()['id']
        for module in (None, '通信'):
            response = client.post(f'/api/projects/{project}/imports', headers=headers(),
                                   data={'module':module} if module else {}, files={'files':('doc.txt',b'content','text/plain')})
            assert response.status_code == 202
            assert process_one(app.state.engine, root, 'worker', datetime.now(timezone.utc))
            job = client.get('/api/imports/' + response.json()['jobs'][0], headers=headers()).json()
            knowledge = client.get('/api/knowledge/' + job['result']['knowledge_id'], headers=headers()).json()
            assert knowledge['module'] == (module or '公共知识')
            assert client.post('/api/knowledge/publish-batch', headers=headers(), json={'version_ids':[knowledge['id']]}).status_code == 200
        assert set(client.get(f'/api/projects/{project}/modules', headers=headers()).json()) == {'公共知识','通信'}
        assert client.get(f'/api/projects/{project}/modules', headers=headers('other')).status_code == 404
        from workbench.questions.library import ProjectLibrary
        library = ProjectLibrary(app.state.engine, 'owner', project, root, 50 * 1024 * 1024)
        assert {item['module'] for item in library.list_documents()['documents']} == {'公共知识','通信'}
        from sqlalchemy.orm import Session
        from workbench.knowledge.models import SourceModule
        with Session(app.state.engine) as db, db.begin():
            old_metadata = db.get(SourceModule, knowledge['source']['id'])
            db.delete(old_metadata)
        assert client.get('/api/knowledge/' + knowledge['id'], headers=headers()).json()['module'] == '公共知识'


def test_skill_registration_and_module_matching_in_analysis(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger='workbench.issue_analysis')
    from workbench.issues import analysis_worker
    root = tmp_path / 'objects'
    app = create_app(f'sqlite:///{tmp_path / "db.sqlite"}', testing=True, dev_users={'owner':'password'}, storage_root=root)
    config = tmp_path / 'config.toml'; config.write_text(f'[analysis]\ncode_root="{tmp_path.as_posix()}"\n')
    captured = []
    class Agent:
        def analyze(self, question, tools):
            captured.append(question)
            return {'answer':'分析结果','citations':[]}
    monkeypatch.setattr(analysis_worker, 'load_agent', lambda _:Agent())
    with TestClient(app) as owner, TestClient(app) as guest:
        project = owner.post('/api/projects', headers=headers(), json={'name':'项目'}).json()['id']
        url = f'/api/projects/{project}/skills'
        def register(module, content):
            return owner.post(url, headers=headers(), json={'module':module,'name':module,'content':content})
        assert register('通信','通信排查步骤').status_code == 201
        assert register('公共知识','通用排查步骤').status_code == 201
        assert register('存储','不应载入').status_code == 201
        other_project = owner.post('/api/projects', headers=headers(), json={'name':'其他'}).json()['id']
        assert owner.post(f'/api/projects/{other_project}/skills', headers=headers(), json={'module':'通信','name':'其他项目','content':'其他项目秘密'}).status_code == 201
        assert owner.get(url, headers=headers('other')).status_code == 404
        assert guest.get(f'/api/guest/projects/{project}/modules').status_code == 401
        guest.post('/api/guest/session')
        assert '通信' in guest.get(f'/api/guest/projects/{project}/modules').json()
        assert guest.get('/api/guest/projects/missing/modules').status_code == 404
        assert guest.post(url, json={'module':'通信','name':'x','content':'y'}).status_code in (401,403)
        issue = guest.post('/api/guest/issues', files={'project_id':(None,project),'module':(None,'通信'),'description':(None,'错误')}).json()['id']
        assert guest.post(f'/api/guest/issues/{issue}/analyze').status_code == 202
        assert analysis_worker.process_analysis_one(app.state.engine, root, config)
        assert len(captured) == 1 and '通信排查步骤' in captured[0] and '通用排查步骤' in captured[0]
        assert '不应载入' not in captured[0]
        assert '其他项目秘密' not in captured[0]
        assert guest.get('/api/guest/issues').json()['items'][0]['analysis']['state'] == 'completed'
        assert 'skills_matched' in caplog.text and '通信排查步骤' not in caplog.text


def test_skill_change_during_analysis_does_not_publish_stale_report(tmp_path, monkeypatch):
    from workbench.issues import analysis_worker
    root = tmp_path / 'objects'
    app = create_app(f'sqlite:///{tmp_path / "db.sqlite"}', testing=True, dev_users={'owner':'password'}, storage_root=root)
    config = tmp_path / 'config.toml'; config.write_text(f'[analysis]\ncode_root="{tmp_path.as_posix()}"\n')
    with TestClient(app) as owner, TestClient(app) as guest:
        project = owner.post('/api/projects', headers=headers(), json={'name':'项目'}).json()['id']
        url = f'/api/projects/{project}/skills'
        skill = owner.post(url, headers=headers(), json={'module':'通信','name':'排查','content':'已停用的步骤'}).json()['id']
        class Agent:
            def analyze(self, question, tools):
                assert owner.delete(f'{url}/{skill}', headers=headers()).status_code == 200
                return {'answer':'不应发布','citations':[]}
        monkeypatch.setattr(analysis_worker, 'load_agent', lambda _:Agent())
        guest.post('/api/guest/session')
        issue = guest.post('/api/guest/issues', files={'project_id':(None,project),'module':(None,'通信'),'description':(None,'错误')}).json()['id']
        guest.post(f'/api/guest/issues/{issue}/analyze')
        assert analysis_worker.process_analysis_one(app.state.engine, root, config)
        result = guest.get('/api/guest/issues').json()['items'][0]['analysis']
        assert result['state'] == 'failed' and result['reason'] == 'skill_changed' and result['report'] is None


def test_skill_matching_normalizes_module_and_enforces_limit(tmp_path, monkeypatch):
    from workbench.issues import analysis_worker
    root = tmp_path / 'objects'
    app = create_app(f'sqlite:///{tmp_path / "db.sqlite"}', testing=True, dev_users={'owner':'password'}, storage_root=root)
    config = tmp_path / 'config.toml'; config.write_text(f'[analysis]\ncode_root="{tmp_path.as_posix()}"\n')
    class Agent:
        def analyze(self, question, tools):
            raise AssertionError('too many skills must fail before calling the model')
    monkeypatch.setattr(analysis_worker, 'load_agent', lambda _:Agent())
    with TestClient(app) as owner, TestClient(app) as guest:
        project = owner.post('/api/projects', headers=headers(), json={'name':'项目'}).json()['id']
        for index in range(11):
            assert owner.post(f'/api/projects/{project}/skills', headers=headers(),
                              json={'module':'ＮＥＴ','name':str(index),'content':'排查步骤'}).status_code == 201
        guest.post('/api/guest/session')
        issue = guest.post('/api/guest/issues', files={'project_id':(None,project),'module':(None,'net'),'description':(None,'错误')}).json()['id']
        guest.post(f'/api/guest/issues/{issue}/analyze')
        assert analysis_worker.process_analysis_one(app.state.engine, root, config)
        assert guest.get('/api/guest/issues').json()['items'][0]['analysis']['reason'] == 'skill_limit'
