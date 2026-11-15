import sqlite3
from unittest.mock import patch
from fastapi.testclient import TestClient
from workbench.app import create_app


def h(user):
    return {'X-User-ID':user, 'X-Principal-Type':'human_session', 'X-CSRF-Token':'test'}


def test_changes_are_audited_without_status_content(tmp_path):
    path = tmp_path / 'test.db'
    client = TestClient(create_app(f'sqlite:///{path}', testing=True))
    created = client.post('/api/projects', headers=h('owner'), json={'name':'Project'})
    pid = created.json()['id']
    client.put(f'/api/projects/{pid}/members/colleague', headers=h('owner'), json={'role':'member'})
    saved = client.put('/api/personal/status', headers=h('owner'), json={'focus':'sensitive status text'})
    snapshot = saved.json()
    client.post(f"/api/personal/status/{snapshot['id']}/publish", headers=h('owner'), json={'expected_version':snapshot['version']})
    client.delete(f'/api/projects/{pid}/members/colleague', headers=h('owner'))
    assert client.put(f'/api/projects/{pid}/members/other', headers=h('stranger'), json={'role':'member'}).status_code == 403
    with sqlite3.connect(path) as db:
        rows = db.execute('select actor_id, action, resource_id, resource_version, request_id, details from audit_events order by rowid').fetchall()
    assert [row[1] for row in rows] == ['project.created','project.member.set','personal.draft.saved','personal.published','project.member.removed']
    assert all(row[0] == 'owner' for row in rows)
    assert rows[0][4] == created.headers['X-Request-ID']
    assert rows[2][3] == snapshot['version']
    assert 'sensitive status text' not in str(rows)


def test_audit_failure_rolls_back_business_change(tmp_path):
    path = tmp_path / 'test.db'
    app = create_app(f'sqlite:///{path}', testing=True)
    with TestClient(app, raise_server_exceptions=False) as client:
        with patch('workbench.app.record_audit', side_effect=RuntimeError('injected failure')):
            response = client.put('/api/personal/status', headers=h('owner'), json={'focus':'must roll back'})
        assert response.status_code == 500
        assert client.get('/api/personal/status', headers=h('owner')).json() is None
