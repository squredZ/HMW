from fastapi.testclient import TestClient
from workbench.app import create_app


def test_anonymous_and_untrusted_identity_cannot_write(tmp_path):
    client = TestClient(create_app(f"sqlite:///{tmp_path / 'test.db'}", testing=True))
    assert client.put('/api/personal/status', json={'focus': 'private'}).status_code == 401
    assert client.put('/api/personal/status', headers={'X-User-ID': 'owner', 'X-Principal-Type': 'agent'}, json={'focus': 'private', 'source': 'manual'}).status_code in (401, 403, 422)


def test_owner_draft_publish_and_stale_write(tmp_path):
    client = TestClient(create_app(f"sqlite:///{tmp_path / 'test.db'}", testing=True))
    owner = {'X-User-ID': 'owner', 'X-Principal-Type': 'human_session', 'X-CSRF-Token': 'test'}
    saved = client.put('/api/personal/status', headers=owner, json={'focus': 'Release', 'progress': 40})
    assert saved.status_code == 200
    status = saved.json()
    assert status['state'] == 'draft'
    assert client.post(f"/api/personal/status/{status['id']}/publish", headers=owner, json={'expected_version': status['version']}).status_code == 200
    assert client.put('/api/personal/status', headers=owner, json={'focus': 'stale', 'expected_version': status['version']}).status_code == 409
    assert client.put('/api/personal/status', headers=owner, json={'focus': 'invalid', 'progress': 101}).status_code == 422


def test_other_user_cannot_publish_or_read_owners_status(tmp_path):
    client = TestClient(create_app(f"sqlite:///{tmp_path / 'test.db'}", testing=True))
    owner = {'X-User-ID': 'owner', 'X-Principal-Type': 'human_session', 'X-CSRF-Token': 'test'}
    other = {'X-User-ID': 'colleague', 'X-Principal-Type': 'human_session', 'X-CSRF-Token': 'test'}
    saved = client.put('/api/personal/status', headers=owner, json={'focus': 'confidential'}).json()
    assert client.get('/api/personal/status', headers=other).json() is None
    response = client.post(f"/api/personal/status/{saved['id']}/publish", headers=other, json={'expected_version': saved['version']})
    assert response.status_code == 404


def test_production_does_not_trust_testing_headers(tmp_path):
    client = TestClient(create_app(f"sqlite:///{tmp_path / 'test.db'}"))
    headers = {'X-User-ID': 'owner', 'X-Principal-Type': 'human_session', 'X-CSRF-Token': 'test'}
    assert client.put('/api/personal/status', headers=headers, json={'focus': 'unsafe'}).status_code == 401


def test_client_cannot_choose_owner_and_csrf_is_required(tmp_path):
    client = TestClient(create_app(f"sqlite:///{tmp_path / 'test.db'}", testing=True))
    headers = {'X-User-ID': 'colleague', 'X-Principal-Type': 'human_session', 'X-CSRF-Token': 'test'}
    assert client.put('/api/personal/status', headers=headers, json={'owner_id': 'owner'}).status_code == 422
    del headers['X-CSRF-Token']
    assert client.put('/api/personal/status', headers=headers, json={'focus': 'unsafe'}).status_code == 403


def test_development_session_requires_credentials_and_csrf(tmp_path):
    client = TestClient(create_app(f"sqlite:///{tmp_path / 'test.db'}", dev_users={'owner': 'local-password'}))
    assert client.post('/api/dev/session', json={'username': 'owner', 'password': 'wrong'}).status_code == 401
    login = client.post('/api/dev/session', json={'username': 'owner', 'password': 'local-password'})
    assert login.status_code == 200
    assert client.put('/api/personal/status', json={'focus': 'work'}).status_code == 403
    saved = client.put('/api/personal/status', headers={'X-CSRF-Token': login.json()['csrf_token']}, json={'focus': 'work'})
    assert saved.status_code == 200
    assert client.get('/api/personal/status').json()['focus'] == 'work'
    assert client.delete('/api/dev/session', headers={'X-CSRF-Token': login.json()['csrf_token']}).status_code == 204
    assert client.get('/api/personal/status').status_code == 401
