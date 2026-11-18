from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from workbench.app import create_app
from workbench.audit import AuditEvent
from workbench.issues.models import Issue


def test_replayed_submission_is_deduplicated_and_different_content_conflicts(tmp_path):
    root = tmp_path / 'objects'
    app = create_app(f'sqlite:///{tmp_path / "db.sqlite"}', dev_users={'owner':'password'}, storage_root=root)
    with TestClient(app) as owner, TestClient(app) as guest, TestClient(app) as other:
        csrf = {'X-CSRF-Token':owner.post('/api/dev/session', json={'username':'owner','password':'password'}).json()['csrf_token']}
        project = owner.post('/api/projects', headers=csrf, json={'name':'项目'}).json()['id']
        guest.post('/api/guest/session')
        other.post('/api/guest/session')
        headers = {'Idempotency-Key':str(uuid4())}
        fields = {'project_id':project, 'module':'模块', 'description':'问题'}
        def post(client, body=fields, content=b'error'):
            return client.post('/api/guest/issues', headers=headers, data=body, files={'log':('log.txt',content,'text/plain')})
        first, repeated = post(guest), post(guest)
        assert first.status_code == repeated.status_code == 201
        assert first.json() == repeated.json()
        assert post(guest, content=b'different').status_code == 409
        assert post(other).json()['id'] != first.json()['id']
        assert guest.post('/api/guest/issues', headers={'Idempotency-Key':'invalid'}, data=fields,
                          files={'log':('log.txt',b'error','text/plain')}).status_code == 422
        assert len(list(root.iterdir())) == 2
        with Session(app.state.engine) as db:
            assert db.scalar(select(func.count()).select_from(Issue)) == 2
            assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == 'issue.submitted')) == 2


def test_concurrent_retries_create_only_one_issue(tmp_path):
    root = tmp_path / 'objects'
    app = create_app(f'sqlite:///{tmp_path / "db.sqlite"}', dev_users={'owner':'password'}, storage_root=root)
    with TestClient(app) as owner, TestClient(app) as guest, TestClient(app) as second:
        csrf = {'X-CSRF-Token':owner.post('/api/dev/session', json={'username':'owner','password':'password'}).json()['csrf_token']}
        project = owner.post('/api/projects', headers=csrf, json={'name':'项目'}).json()['id']
        guest.post('/api/guest/session')
        second.cookies.set('hmw_guest', guest.cookies.get('hmw_guest'))
        headers = {'Idempotency-Key':str(uuid4())}
        def post(client):
            return client.post('/api/guest/issues', headers=headers, data={'project_id':project, 'module':'模块', 'description':'问题'},
                               files={'log':('log.txt',b'error','text/plain')})
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(post, (guest, second)))
        assert [response.status_code for response in results] == [201, 201]
        assert results[0].json() == results[1].json()
        assert len(list(root.iterdir())) == 1
        with Session(app.state.engine) as db:
            assert db.scalar(select(func.count()).select_from(Issue)) == 1
