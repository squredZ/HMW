from fastapi.testclient import TestClient
from workbench.app import create_app


def headers(user):
    return {'X-User-ID': user, 'X-Principal-Type': 'human_session', 'X-CSRF-Token': 'test'}


def test_project_membership_is_scoped_and_only_owner_manages_members(tmp_path):
    client = TestClient(create_app(f"sqlite:///{tmp_path / 'projects.db'}", testing=True))
    created = client.post('/api/projects', headers=headers('owner'), json={'name': 'Alpha'})
    assert created.status_code == 200
    project_id = created.json()['id']
    assert client.get('/api/projects', headers=headers('outsider')).json() == []
    assert client.put(f'/api/projects/{project_id}/members/outsider', headers=headers('outsider'), json={'role': 'owner'}).status_code in (403, 404, 422)
    assert client.put(f'/api/projects/{project_id}/members/member', headers=headers('owner'), json={'role': 'member'}).status_code == 200
    assert client.get('/api/projects', headers=headers('member')).json()[0]['name'] == 'Alpha'
    assert client.put(f'/api/projects/{project_id}/members/member', headers=headers('member'), json={'role': 'maintainer'}).status_code == 403
    assert client.put(f'/api/projects/{project_id}/members/owner', headers=headers('owner'), json={'role': 'member'}).status_code == 403
