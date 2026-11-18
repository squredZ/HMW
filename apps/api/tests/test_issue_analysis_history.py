from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from workbench.app import create_app
from workbench.issues.analysis_models import IssueAnalysis, IssueAnalysisUsage


def test_shared_history_is_paginated_and_missing_usage_is_unknown(tmp_path):
    app = create_app(f'sqlite:///{tmp_path / "db.sqlite"}', dev_users={'owner':'password'}, storage_root=tmp_path / 'objects')
    with TestClient(app) as owner, TestClient(app) as guest, TestClient(app) as other:
        csrf = {'X-CSRF-Token':owner.post('/api/dev/session', json={'username':'owner','password':'password'}).json()['csrf_token']}
        project = owner.post('/api/projects', headers=csrf, json={'name':'项目'}).json()['id']
        guest.post('/api/guest/session')
        other.post('/api/guest/session')
        issue = guest.post('/api/guest/issues', data={'project_id':project,'module':'模块','description':'故障'},
                           files={'log':('test.log', b'failure', 'text/plain')}).json()['id']
        usage = {'input':10,'output':5,'cache_read':0,'cache_write':0,'total':15,'partial':False}
        with Session(app.state.engine) as db, db.begin():
            db.add_all([IssueAnalysis(id='older', issue_id=issue, state='failed', created_at=1),
                        IssueAnalysis(id='newer', issue_id=issue, state='completed', created_at=2)])
            db.flush()
            db.add(IssueAnalysisUsage(analysis_id='newer', usage=usage))
        first = other.get(f'/api/guest/issues/{issue}/analyses?limit=1').json()
        assert first['has_more'] and first['items'][0]['usage'] == usage
        second = other.get(f'/api/guest/issues/{issue}/analyses?limit=1&offset=1').json()
        assert not second['has_more'] and second['items'][0]['usage'] is None
        assert other.get('/api/guest/issues').json()['items'][0]['analysis']['usage'] == usage
        assert other.get('/api/guest/issues/unknown/analyses').status_code == 404
        assert other.get(f'/api/guest/issues/{issue}/analyses?limit=101').status_code == 422
