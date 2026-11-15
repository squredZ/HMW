from fastapi.testclient import TestClient
from workbench.app import create_app


def h(user):
    return {'X-User-ID': user, 'X-Principal-Type': 'human_session', 'X-CSRF-Token': 'test'}


def test_published_status_filters_allocations_and_survives_draft(tmp_path):
    client = TestClient(create_app(f"sqlite:///{tmp_path / 'test.db'}", testing=True))
    a = client.post('/api/projects', headers=h('owner'), json={'name':'A'}).json()['id']
    b = client.post('/api/projects', headers=h('owner'), json={'name':'B'}).json()['id']
    assert client.put(f'/api/projects/{a}/members/colleague', headers=h('owner'), json={'role':'member'}).status_code == 200
    payload = {'focus':'published', 'audience_project_ids':[a,b], 'allocations':[{'project_id':a,'week':'2026-11-09','unit':'hours','planned':4,'actual':None}, {'project_id':b,'week':'2026-11-09','unit':'percent','planned':30,'actual':10}]}
    saved = client.put('/api/personal/status', headers=h('owner'), json=payload).json()
    assert client.get('/api/personal/status/owner', headers=h('colleague')).status_code == 404
    published = client.post(f"/api/personal/status/{saved['id']}/publish", headers=h('owner'), json={'expected_version':saved['version']}).json()
    view = client.get('/api/personal/status/owner', headers=h('colleague')).json()
    assert view['focus'] == 'published'
    assert len(view['allocations']) == 1
    assert view['allocations'][0]['actual'] is None
    assert b not in str(view)
    assert client.get('/api/personal/status/owner', headers=h('stranger')).status_code == 404
    payload['focus'] = 'private draft'
    payload['expected_version'] = published['version']
    assert client.put('/api/personal/status', headers=h('owner'), json=payload).status_code == 200
    assert client.get('/api/personal/status/owner', headers=h('colleague')).json()['focus'] == 'published'


def test_owner_cannot_share_or_allocate_to_unknown_project(tmp_path):
    client = TestClient(create_app(f"sqlite:///{tmp_path / 'test.db'}", testing=True))
    assert client.put('/api/personal/status', headers=h('owner'), json={'audience_project_ids':['unknown']}).status_code in (403, 422)
    assert client.put('/api/personal/status', headers=h('owner'), json={'allocations':[{'project_id':'unknown','week':'2026-11-09','unit':'hours','planned':1}]}).status_code in (403, 422)


def test_revoked_membership_blocks_published_status(tmp_path):
    client = TestClient(create_app(f"sqlite:///{tmp_path / 'test.db'}", testing=True))
    project = client.post('/api/projects', headers=h('owner'), json={'name':'A'}).json()['id']
    client.put(f'/api/projects/{project}/members/colleague', headers=h('owner'), json={'role':'member'})
    saved = client.put('/api/personal/status', headers=h('owner'), json={'focus':'shared', 'audience_project_ids':[project]}).json()
    client.post(f"/api/personal/status/{saved['id']}/publish", headers=h('owner'), json={'expected_version':saved['version']})
    assert client.get('/api/personal/status/owner', headers=h('colleague')).status_code == 200
    assert client.delete(f'/api/projects/{project}/members/colleague', headers=h('colleague')).status_code == 403
    assert client.delete(f'/api/projects/{project}/members/colleague', headers=h('owner')).status_code == 204
    assert client.get('/api/personal/status/owner', headers=h('colleague')).status_code == 404


def test_two_development_sessions_share_only_authorized_status(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'test.db'}", testing=True, dev_users={'owner':'owner-local', 'colleague':'colleague-local'})
    owner, colleague = TestClient(app), TestClient(app)
    owner_token = owner.post('/api/dev/session', json={'username':'owner','password':'owner-local'}).json()['csrf_token']
    colleague.post('/api/dev/session', json={'username':'colleague','password':'colleague-local'})
    csrf = {'X-CSRF-Token':owner_token}
    project = owner.post('/api/projects', headers=csrf, json={'name':'A'}).json()['id']
    owner.put(f'/api/projects/{project}/members/colleague', headers=csrf, json={'role':'member'})
    saved = owner.put('/api/personal/status', headers=csrf, json={'focus':'shared','audience_project_ids':[project]}).json()
    owner.post(f"/api/personal/status/{saved['id']}/publish", headers=csrf, json={'expected_version':saved['version']})
    assert colleague.get('/api/personal/status/owner').json()['focus'] == 'shared'
    owner.delete(f'/api/projects/{project}/members/colleague', headers=csrf)
    assert colleague.get('/api/personal/status/owner').status_code == 404
