from fastapi.testclient import TestClient

from workbench.app import create_app
from test_guest_questions import submit


def test_visitor_submits_issue_owner_reviews_and_resolves(tmp_path):
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner':'password'}, storage_root=root)
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf = {'X-CSRF-Token':owner.post('/api/dev/session',json={'username':'owner','password':'password'}).json()['csrf_token']}
        project = owner.post('/api/projects',headers=csrf,json={'name':'项目'}).json()['id']
        payload = {'project_id':project,'title':'接口异常','description':'请求返回错误'}
        assert guest.post('/api/guest/issues',json=payload).status_code == 401
        guest.post('/api/guest/session')
        assert guest.post('/api/guest/issues',json=payload).status_code == 404
        version = submit(owner, app, root, project, csrf, '公开资料', share=True)
        response = guest.post('/api/guest/issues',json=payload)
        assert response.status_code == 201
        issue_id = response.json()['id']
        assert guest.get('/api/issues').status_code == 401
        listing = owner.get('/api/issues')
        assert listing.headers['cache-control'] == 'no-store'
        issue = listing.json()['items'][0]
        assert issue['id'] == issue_id and issue['description'] == '请求返回错误' and issue['state'] == 'new'
        assert owner.post(f'/api/issues/{issue_id}/resolve').status_code == 403
        assert owner.post(f'/api/issues/{issue_id}/resolve',headers=csrf).status_code == 200
        assert owner.post(f'/api/issues/{issue_id}/resolve',headers=csrf).status_code == 409
        assert owner.get('/api/issues').json()['items'][0]['state'] == 'resolved'
        owner.put(f'/api/knowledge/{version}/guest-visibility',headers=csrf,json={'visible':False})
        assert guest.post('/api/guest/issues',json=payload).status_code == 404
        assert owner.get('/api/issues').json()['items'][0]['id'] == issue_id
        assert guest.post('/api/guest/issues',json={**payload,'title':' '}).status_code == 422


def test_issue_intake_limits_streamed_body_without_content_length(tmp_path):
    import asyncio
    from workbench.issues.body_limit import IssueBodyLimit

    async def exercise():
        messages = iter([{'type':'http.request','body':b'x' * 200000,'more_body':True},
                         {'type':'http.request','body':b'x' * 100000,'more_body':False}])
        sent = []
        async def receive():
            return next(messages)
        async def send(message):
            sent.append(message)
        async def app(scope, receive, send):
            raise AssertionError('oversized body reached application')
        await IssueBodyLimit(app)({'type':'http','path':'/api/guest/issues','method':'POST','headers':[]}, receive, send)
        assert sent[0]['status'] == 413
    asyncio.run(exercise())


def test_issue_list_and_resolution_exclude_other_projects(tmp_path):
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", testing=True, dev_users={'owner':'password'}, storage_root=root)
    owner_headers = {'X-User-Id':'owner', 'X-Principal-Type':'human_session', 'X-CSRF-Token':'test'}
    other_headers = {'X-User-Id':'other', 'X-Principal-Type':'human_session', 'X-CSRF-Token':'test'}
    with TestClient(app) as owner, TestClient(app) as guest, TestClient(app) as other:
        csrf = {'X-CSRF-Token':owner.post('/api/dev/session',json={'username':'owner','password':'password'}).json()['csrf_token']}
        project = owner.post('/api/projects',headers=csrf,json={'name':'项目'}).json()['id']
        submit(owner, app, root, project, csrf, '公开资料', share=True)
        guest.post('/api/guest/session')
        issue_id = guest.post('/api/guest/issues',json={'project_id':project,'title':'问题','description':'描述'}).json()['id']
        assert other.get('/api/issues',headers=other_headers).json()['items'] == []
        assert other.post(f'/api/issues/{issue_id}/resolve',headers=other_headers).status_code == 404
        assert owner.get('/api/issues',headers=owner_headers).json()['items'][0]['state'] == 'new'
