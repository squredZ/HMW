from uuid import uuid4
from fastapi.testclient import TestClient
from workbench.app import create_app


def test_issue_without_log_can_be_retried_listed_and_analyzed(tmp_path, monkeypatch):
    from workbench.issues import analysis_worker

    root = tmp_path / 'objects'
    app = create_app(f'sqlite:///{tmp_path / "db.sqlite"}', dev_users={'owner':'password'}, storage_root=root)
    config = tmp_path / 'config.toml'
    config.write_text(f'[analysis]\ncode_root="{tmp_path.as_posix()}"\n')
    class Agent:
        def analyze(self, question, tools):
            assert '通信模块' in question
            assert '未提供日志' in question
            assert tools.log_path is None
            return {'answer':'需要更多信息。', 'citations':[]}
    monkeypatch.setattr(analysis_worker, 'load_agent', lambda _: Agent())
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf = {'X-CSRF-Token':owner.post('/api/dev/session', json={'username':'owner','password':'password'}).json()['csrf_token']}
        project = owner.post('/api/projects', headers=csrf, json={'name':'项目'}).json()['id']
        guest.post('/api/guest/session')
        fields = {'project_id':(None,project), 'module':(None,'通信模块'), 'description':(None,'连接失败')}
        headers = {'Idempotency-Key':str(uuid4())}
        first = guest.post('/api/guest/issues', headers=headers, files=fields)
        assert first.status_code == 201
        retry = guest.post('/api/guest/issues', headers=headers, files=fields)
        assert retry.json() == first.json()
        changed = guest.post('/api/guest/issues', headers=headers, files={**fields,'module':(None,'其他模块')})
        assert changed.status_code == 409
        issue = first.json()['id']
        item = guest.get('/api/guest/issues').json()['items'][0]
        assert item['module'] == '通信模块' and item['log_filename'] == ''
        assert guest.post(f'/api/guest/issues/{issue}/analyze').status_code == 202
        assert analysis_worker.process_analysis_one(app.state.engine, root, config)
        item = guest.get('/api/guest/issues').json()['items'][0]
        assert item['analysis']['state'] == 'completed'
        assert item['analysis']['report'] == '需要更多信息。'
