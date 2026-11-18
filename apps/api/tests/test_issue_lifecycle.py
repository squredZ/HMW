import logging
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.app import create_app
from workbench.audit import AuditEvent


def test_owner_can_move_reopen_and_close_issue_with_audited_versions(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger='workbench.issues')
    app = create_app(f'sqlite:///{tmp_path / "db.sqlite"}', testing=True, dev_users={'owner':'password'}, storage_root=tmp_path / 'objects')
    owner_headers = {'X-User-Id':'owner', 'X-Principal-Type':'human_session', 'X-CSRF-Token':'test'}
    with TestClient(app) as owner, TestClient(app) as guest:
        project = owner.post('/api/projects', headers=owner_headers, json={'name':'项目'}).json()['id']
        guest.post('/api/guest/session')
        issue = guest.post('/api/guest/issues', data={'project_id':project,'module':'模块','description':'故障'},
                           files={'log':('app.log',b'error','text/plain')}).json()['id']
        url = f'/api/issues/{issue}/transition'
        assert owner.get('/api/issues', headers=owner_headers).json()['items'][0]['version'] == 1
        assert guest.post(url, json={'state':'triaged','reason':'已分诊','expected_version':1}).status_code in (401, 403)
        assert owner.post(url, headers=owner_headers, json={'state':'closed','reason':'无效','expected_version':1}).status_code == 409
        assert owner.post(url, headers=owner_headers, json={'state':'triaged','reason':'   ','expected_version':1}).status_code == 422
        transitions = [('needs_info','需要日志'),('triaged','已分诊'),('in_progress','开始排查'),('resolved','已修复'),
                       ('in_progress','问题复现'),('resolved','再次修复'),('closed','已确认')]
        for version, (state, reason) in enumerate(transitions, start=1):
            response = owner.post(url, headers=owner_headers, json={'state':state,'reason':reason,'expected_version':version})
            assert response.status_code == 200
            assert response.json() == {'id':issue, 'state':state, 'version':version + 1}
        assert owner.post(url, headers=owner_headers, json={'state':'in_progress','reason':'无效','expected_version':8}).status_code == 409
        assert owner.post(url, headers={**owner_headers, 'X-User-Id':'other'},
                          json={'state':'in_progress','reason':'越权','expected_version':8}).status_code == 404
        assert guest.get('/api/guest/issues').json()['items'][0]['state'] == 'closed'
        with Session(app.state.engine) as db:
            events = db.scalars(select(AuditEvent).where(AuditEvent.action == 'issue.transitioned')
                                .order_by(AuditEvent.resource_version)).all()
            assert [event.resource_version for event in events] == list(range(2, 9))
            assert events[4].details['reason'] == '问题复现'
        assert 'issue_transitioned' in caplog.text
        assert '问题复现' not in caplog.text


def test_competing_transitions_allow_only_one_winner(tmp_path):
    app = create_app(f'sqlite:///{tmp_path / "db.sqlite"}', testing=True, dev_users={'owner':'password'}, storage_root=tmp_path / 'objects')
    headers = {'X-User-Id':'owner', 'X-Principal-Type':'human_session', 'X-CSRF-Token':'test'}
    with TestClient(app) as first, TestClient(app) as second, TestClient(app) as guest:
        project = first.post('/api/projects', headers=headers, json={'name':'项目'}).json()['id']
        guest.post('/api/guest/session')
        issue = guest.post('/api/guest/issues', data={'project_id':project,'module':'模块','description':'故障'},
                           files={'log':('app.log',b'error','text/plain')}).json()['id']
        def transition(client):
            return client.post(f'/api/issues/{issue}/transition', headers=headers,
                               json={'state':'triaged','reason':'并发处理','expected_version':1})
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(transition, (first, second)))
        assert sorted(response.status_code for response in results) == [200, 409]
        with Session(app.state.engine) as db:
            events = db.scalars(select(AuditEvent).where(AuditEvent.action == 'issue.transitioned')).all()
            assert len(events) == 1 and events[0].resource_version == 2


def test_legacy_resolution_records_version_and_can_be_reopened(tmp_path):
    app = create_app(f'sqlite:///{tmp_path / "db.sqlite"}', testing=True, dev_users={'owner':'password'}, storage_root=tmp_path / 'objects')
    headers = {'X-User-Id':'owner', 'X-Principal-Type':'human_session', 'X-CSRF-Token':'test'}
    with TestClient(app) as owner, TestClient(app) as guest:
        project = owner.post('/api/projects', headers=headers, json={'name':'项目'}).json()['id']
        guest.post('/api/guest/session')
        issue = guest.post('/api/guest/issues', data={'project_id':project,'module':'模块','description':'故障'},
                           files={'log':('app.log',b'error','text/plain')}).json()['id']
        assert owner.post(f'/api/issues/{issue}/resolve', headers=headers).status_code == 200
        with Session(app.state.engine) as db:
            event = db.scalar(select(AuditEvent).where(AuditEvent.action == 'issue.resolved'))
            assert event.resource_version == 2
        assert owner.post(f'/api/issues/{issue}/transition', headers=headers,
                          json={'state':'in_progress','reason':'问题复现','expected_version':2}).status_code == 200


def test_audit_failure_rolls_back_transition(tmp_path):
    app = create_app(f'sqlite:///{tmp_path / "db.sqlite"}', testing=True, dev_users={'owner':'password'}, storage_root=tmp_path / 'objects')
    headers = {'X-User-Id':'owner', 'X-Principal-Type':'human_session', 'X-CSRF-Token':'test'}
    with TestClient(app, raise_server_exceptions=False) as owner, TestClient(app) as guest:
        project = owner.post('/api/projects', headers=headers, json={'name':'项目'}).json()['id']
        guest.post('/api/guest/session')
        issue = guest.post('/api/guest/issues', data={'project_id':project,'module':'模块','description':'故障'},
                           files={'log':('app.log',b'error','text/plain')}).json()['id']
        with patch('workbench.app.record_audit', side_effect=RuntimeError('audit unavailable')):
            response = owner.post(f'/api/issues/{issue}/transition', headers=headers,
                                  json={'state':'triaged','reason':'分诊','expected_version':1})
        assert response.status_code == 500
        item = owner.get('/api/issues', headers=headers).json()['items'][0]
        assert item['state'] == 'new' and item['version'] == 1
