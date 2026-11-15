import sqlite3
from fastapi.testclient import TestClient
from workbench.app import create_app


def h(user):
    return {'X-User-ID':user, 'X-Principal-Type':'human_session', 'X-CSRF-Token':'test'}


def test_only_owner_can_withdraw_published_status_even_after_new_draft(tmp_path):
    client = TestClient(create_app(f"sqlite:///{tmp_path / 'test.db'}", testing=True))
    pid = client.post('/api/projects', headers=h('owner'), json={'name':'Project'}).json()['id']
    client.put(f'/api/projects/{pid}/members/colleague', headers=h('owner'), json={'role':'member'})
    draft = client.put('/api/personal/status', headers=h('owner'), json={'focus':'old','audience_project_ids':[pid]}).json()
    pub = client.post(f"/api/personal/status/{draft['id']}/publish", headers=h('owner'), json={'expected_version':draft['version']}).json()
    edited = client.put('/api/personal/status', headers=h('owner'), json={'focus':'draft','expected_version':pub['version']}).json()
    path = f"/api/personal/status/{draft['id']}/withdraw"
    assert client.post(path, headers=h('colleague'), json={'expected_version':edited['version']}).status_code == 404
    assert client.post(path, headers=h('owner'), json={'expected_version':pub['version']}).status_code == 409
    result = client.post(path, headers=h('owner'), json={'expected_version':edited['version']})
    assert result.status_code == 200
    assert result.json()['state'] == 'withdrawn'
    assert client.get('/api/personal/status/owner', headers=h('colleague')).status_code == 404
    assert client.post(path, headers=h('owner'), json={'expected_version':result.json()['version']}).status_code == 409
    with sqlite3.connect(tmp_path / 'test.db') as db:
        events = db.execute("select actor_id, resource_version from audit_events where action = 'personal.withdrawn'").fetchall()
    assert events == [('owner', result.json()['version'])]


def test_withdrawn_status_requires_new_draft_before_republication(tmp_path):
    client = TestClient(create_app(f"sqlite:///{tmp_path / 'test.db'}", testing=True))
    saved = client.put('/api/personal/status', headers=h('owner'), json={'focus':'original'}).json()
    pub = client.post(f"/api/personal/status/{saved['id']}/publish", headers=h('owner'), json={'expected_version':saved['version']}).json()
    withdrawn = client.post(f"/api/personal/status/{saved['id']}/withdraw", headers=h('owner'), json={'expected_version':pub['version']}).json()
    assert client.post(f"/api/personal/status/{saved['id']}/publish", headers=h('owner'), json={'expected_version':withdrawn['version']}).status_code == 409
    draft = client.put('/api/personal/status', headers=h('owner'), json={'focus':'new','expected_version':withdrawn['version']}).json()
    assert client.post(f"/api/personal/status/{saved['id']}/publish", headers=h('owner'), json={'expected_version':draft['version']}).status_code == 200
