from fastapi.testclient import TestClient

from workbench.app import create_app
from test_guest_questions import submit


def test_manage_project_knowledge_and_retire_shared_version(tmp_path):
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner': 'password'}, storage_root=root)
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf = {'X-CSRF-Token': owner.post('/api/dev/session', json={'username': 'owner', 'password': 'password'}).json()['csrf_token']}
        project = owner.post('/api/projects', headers=csrf, json={'name': '项目'}).json()['id']
        other = owner.post('/api/projects', headers=csrf, json={'name': '其他'}).json()['id']
        version = submit(owner, app, root, project, csrf, '知识正文', share=True)
        submit(owner, app, root, other, csrf, '其他项目', share=None)
        source = owner.get(f'/api/knowledge/{version}').json()['source']['id']
        guest.post('/api/guest/session')
        assert guest.get(f'/api/projects/{project}/knowledge').status_code == 401
        listing = owner.get(f'/api/projects/{project}/knowledge', params={'state': 'published', 'q': 'source'}).json()
        assert len(listing['items']) == 1
        assert listing['items'][0]['id'] == version
        assert listing['items'][0]['guest_visible'] is True
        assert owner.get(f'/api/projects/{project}/knowledge', params={'q': 'missing'}).json()['items'] == []
        assert owner.get(f'/api/projects/{project}/knowledge', params={'state': 'bad'}).status_code == 422
        assert guest.get(f'/api/guest/knowledge/{version}').status_code == 200
        assert owner.post(f'/api/knowledge/{version}/retire', headers=csrf).status_code == 200
        assert guest.get(f'/api/guest/knowledge/{version}').status_code == 404
        assert guest.get(f'/api/guest/sources/{source}/download').status_code == 404
        detail = owner.get(f'/api/knowledge/{version}').json()
        assert detail['state'] == 'retired' and detail['content'] == '知识正文'
        assert detail['guest_visible'] is False
        assert owner.get(f'/api/sources/{source}/download').content == '知识正文'.encode()
        assert owner.post(f'/api/knowledge/{version}/retire', headers=csrf).status_code == 409
        assert owner.get(f'/api/projects/{project}/knowledge', params={'state': 'retired'}).json()['items'][0]['id'] == version
