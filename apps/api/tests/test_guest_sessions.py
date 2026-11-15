from fastapi.testclient import TestClient
from workbench.app import create_app
from workbench.jobs.worker import process_one


def test_guests_access_only_explicitly_shared_content_and_cannot_manage(tmp_path):
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner':'password'}, storage_root=root)
    with TestClient(app) as owner, TestClient(app) as first, TestClient(app) as second:
        owner_csrf = owner.post('/api/dev/session', json={'username':'owner','password':'password'}).json()['csrf_token']
        auth = {'X-CSRF-Token':owner_csrf}
        a = first.post('/api/guest/session')
        b = second.post('/api/guest/session')
        assert a.status_code == b.status_code == 200
        assert first.cookies['hmw_guest'] != second.cookies['hmw_guest']
        assert first.put('/api/personal/status', json={'focus':'hijack'}).status_code == 401
        assert first.post('/api/projects', json={'name':'hijack'}).status_code == 401
        assert first.get('/api/guest/status').status_code == 404
        project = owner.post('/api/projects', headers=auth, json={'name':'Public project'}).json()['id']
        draft = owner.put('/api/personal/status', headers=auth, json={'focus':'hidden'}).json()
        pub = owner.post(f"/api/personal/status/{draft['id']}/publish", headers=auth, json={'expected_version':draft['version']}).json()
        assert first.get('/api/guest/status').status_code == 404
        shared = owner.put('/api/personal/status', headers=auth, json={'focus':'shared', 'guest_visible':True, 'expected_version':pub['version']}).json()
        owner.post(f"/api/personal/status/{draft['id']}/publish", headers=auth, json={'expected_version':shared['version']})
        status = first.get('/api/guest/status').json()
        assert status['focus'] == 'shared'
        assert status['updated_at'].endswith('+00:00')
        assert second.get('/api/guest/status').json()['focus'] == 'shared'
        job = owner.post(f'/api/projects/{project}/imports', headers=auth, files=[('files',('a.txt',b'published text','text/plain'))]).json()['jobs'][0]
        assert process_one(app.state.engine, root, 'worker')
        version_id = owner.get(f'/api/imports/{job}').json()['result']['knowledge_id']
        assert first.get(f'/api/guest/knowledge/{version_id}').status_code == 404
        assert first.get('/api/guest/projects').json() == []
        assert owner.post('/api/knowledge/publish-batch', headers=auth, json={'version_ids':[version_id], 'guest_visible':True}).status_code == 200
        assert first.get('/api/guest/projects').json()[0]['id'] == project
        entry = first.get(f'/api/guest/knowledge/{version_id}').json()
        assert entry['content'] == 'published text'
        assert first.get(f"/api/guest/sources/{entry['source']['id']}/download").content == b'published text'
        assert first.get(f'/api/imports/{job}').status_code == 401
        assert first.post('/api/knowledge/publish-batch', json={'version_ids':[version_id], 'guest_visible':True}).status_code == 401


def test_guest_cookie_cannot_forge_owner_or_be_reused_after_clearing(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner':'password'})
    with TestClient(app) as client:
        assert client.get('/api/guest/status').status_code == 401
        client.post('/api/guest/session')
        assert client.put('/api/personal/status', headers={'X-User-ID':'owner','X-Principal-Type':'human_session','X-CSRF-Token':'test'}, json={'focus':'hijack'}).status_code == 401
        client.cookies.clear()
        assert client.get('/api/guest/status').status_code == 401


def test_production_style_app_has_only_one_owner_and_no_member_management(tmp_path):
    import pytest
    database = f"sqlite:///{tmp_path / 'db.sqlite'}"
    with pytest.raises(ValueError):
        create_app(database, dev_users={'owner':'p', 'colleague':'q'})
    app = create_app(database, dev_users={'owner':'password'})
    with TestClient(app) as owner:
        csrf = owner.post('/api/dev/session', json={'username':'owner','password':'password'}).json()['csrf_token']
        auth = {'X-CSRF-Token':csrf}
        project = owner.post('/api/projects', headers=auth, json={'name':'A'}).json()['id']
        assert owner.put(f'/api/projects/{project}/members/colleague', headers=auth, json={'role':'member'}).status_code == 404
        assert owner.delete(f'/api/projects/{project}/members/colleague', headers=auth).status_code == 404


def test_owner_can_revoke_guest_access_without_deleting_published_knowledge(tmp_path):
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner':'password'}, storage_root=root)
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf = owner.post('/api/dev/session', json={'username':'owner','password':'password'}).json()['csrf_token']
        auth = {'X-CSRF-Token':csrf}
        guest.post('/api/guest/session')
        pid = owner.post('/api/projects', headers=auth, json={'name':'A'}).json()['id']
        job = owner.post(f'/api/projects/{pid}/imports', headers=auth, files=[('files',('a.txt',b'content','text/plain'))]).json()['jobs'][0]
        process_one(app.state.engine, root, 'worker')
        version = owner.get(f'/api/imports/{job}').json()['result']['knowledge_id']
        owner.post('/api/knowledge/publish-batch', headers=auth, json={'version_ids':[version], 'guest_visible':True})
        source = guest.get(f'/api/guest/knowledge/{version}').json()['source']['id']
        assert guest.put(f'/api/knowledge/{version}/guest-visibility', json={'visible':False}).status_code == 401
        assert owner.put(f'/api/knowledge/{version}/guest-visibility', headers=auth, json={'visible':False}).status_code == 200
        assert guest.get(f'/api/guest/knowledge/{version}').status_code == 404
        assert guest.get(f'/api/guest/sources/{source}/download').status_code == 404
        assert guest.get('/api/guest/projects').json() == []
        assert owner.get(f'/api/knowledge/{version}').status_code == 200


def test_guest_session_expires_without_exposing_owner_access(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    import workbench.guests as guest_module
    current = [datetime(2026, 11, 15, tzinfo=timezone.utc)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0]
    monkeypatch.setattr(guest_module, 'datetime', Clock)
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner':'password'})
    with TestClient(app) as client:
        client.post('/api/guest/session')
        old = client.cookies['hmw_guest']
        assert client.get('/api/guest/projects').status_code == 200
        current[0] += timedelta(hours=8, seconds=1)
        assert client.get('/api/guest/projects').status_code == 401
        client.post('/api/guest/session')
        assert client.cookies['hmw_guest'] != old
        assert client.get('/api/guest/projects').status_code == 200


def test_concurrent_owner_visibility_updates_are_safe(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy.orm import Session
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner':'password'}, storage_root=root)
    with TestClient(app, raise_server_exceptions=False) as owner:
        csrf = owner.post('/api/dev/session', json={'username':'owner','password':'password'}).json()['csrf_token']
        auth = {'X-CSRF-Token':csrf}
        pid = owner.post('/api/projects', headers=auth, json={'name':'A'}).json()['id']
        job = owner.post(f'/api/projects/{pid}/imports', headers=auth, files=[('files',('a.txt',b'content','text/plain'))]).json()['jobs'][0]
        process_one(app.state.engine, root, 'worker')
        version = owner.get(f'/api/imports/{job}').json()['result']['knowledge_id']
        owner.post('/api/knowledge/publish-batch', headers=auth, json={'version_ids':[version]})
        barrier = Barrier(2, timeout=5)
        original = Session.scalar
        def synchronized(session, statement, *args, **kwargs):
            result = original(session, statement, *args, **kwargs)
            if 'knowledge_versions.id' in str(statement) and 'FOR UPDATE' in str(statement):
                barrier.wait()
            return result
        monkeypatch.setattr(Session, 'scalar', synchronized)
        def share(_):
            return owner.put(f'/api/knowledge/{version}/guest-visibility', headers=auth, json={'visible':True})
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(share, range(2)))
        assert [response.status_code for response in responses] == [200, 200]
        assert owner.get(f'/api/knowledge/{version}').json()['guest_visible'] is True


def test_guest_status_shows_only_explicitly_shared_project_allocations(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner':'password'})
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf = owner.post('/api/dev/session', json={'username':'owner','password':'password'}).json()['csrf_token']
        auth = {'X-CSRF-Token':csrf}
        a = owner.post('/api/projects', headers=auth, json={'name':'公开项目'}).json()['id']
        b = owner.post('/api/projects', headers=auth, json={'name':'隐私项目'}).json()['id']
        body = {'focus':'shared','guest_visible':True,'audience_project_ids':[a], 'allocations':[
            {'project_id':a,'week':'2026-11-09','unit':'hours','planned':4,'actual':None},
            {'project_id':b,'week':'2026-11-09','unit':'percent','planned':20,'actual':10}]}
        draft = owner.put('/api/personal/status', headers=auth, json=body).json()
        owner.post(f"/api/personal/status/{draft['id']}/publish", headers=auth, json={'expected_version':draft['version']})
        guest.post('/api/guest/session')
        view = guest.get('/api/guest/status').json()
        assert len(view['allocations']) == 1
        assert view['allocations'][0]['project_name'] == '公开项目'
        assert view['allocations'][0]['actual'] is None
        assert b not in str(view)
        assert '隐私项目' not in str(view)


def test_guest_responses_are_not_cached(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner':'password'})
    with TestClient(app) as client:
        assert client.get('/api/guest/status').headers['Cache-Control'] == 'no-store'
        assert client.post('/api/guest/session').headers['Cache-Control'] == 'no-store'
        assert client.get('/api/guest/projects').headers['Cache-Control'] == 'no-store'


def test_starting_guest_session_does_not_replace_owner_session(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner':'password'})
    with TestClient(app) as client:
        login = client.post('/api/dev/session', json={'username':'owner','password':'password'})
        csrf = login.json()['csrf_token']
        token = client.cookies['hmw_session']
        assert client.post('/api/guest/session').status_code == 200
        assert client.cookies['hmw_session'] == token
        assert client.put('/api/personal/status', headers={'X-CSRF-Token':csrf}, json={'focus':'owner'}).status_code == 200
        assert client.delete('/api/dev/session', headers={'X-CSRF-Token':csrf}).status_code == 204
        assert client.get('/api/personal/status').status_code == 401
        assert client.get('/api/guest/projects').status_code == 200


def test_withdrawal_revokes_guest_personal_status(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner':'password'})
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf = owner.post('/api/dev/session', json={'username':'owner','password':'password'}).json()['csrf_token']
        auth = {'X-CSRF-Token':csrf}
        guest.post('/api/guest/session')
        draft = owner.put('/api/personal/status', headers=auth, json={'focus':'shared','guest_visible':True}).json()
        published = owner.post(f"/api/personal/status/{draft['id']}/publish", headers=auth, json={'expected_version':draft['version']}).json()
        assert guest.get('/api/guest/status').status_code == 200
        assert owner.post(f"/api/personal/status/{draft['id']}/withdraw", headers=auth, json={'expected_version':published['version']}).status_code == 200
        assert guest.get('/api/guest/status').status_code == 404


def test_legacy_published_status_is_private_by_default_and_preserved(tmp_path):
    import json
    import sqlite3
    path = tmp_path / 'legacy.db'
    database = f'sqlite:///{path}'
    app = create_app(database, dev_users={'owner':'password'})
    with TestClient(app) as owner:
        csrf = owner.post('/api/dev/session', json={'username':'owner','password':'password'}).json()['csrf_token']
        draft = owner.put('/api/personal/status', headers={'X-CSRF-Token':csrf}, json={'focus':'legacy'}).json()
        owner.post(f"/api/personal/status/{draft['id']}/publish", headers={'X-CSRF-Token':csrf}, json={'expected_version':draft['version']})
    with sqlite3.connect(path) as db:
        for table in ('personal_status', 'personal_status_version'):
            rows = db.execute(f'select id, body from {table}').fetchall()
            for row_id, encoded in rows:
                body = json.loads(encoded)
                body.pop('guest_visible', None)
                db.execute(f'update {table} set body = ? where id = ?', (json.dumps(body), row_id))
    migrated = create_app(database, dev_users={'owner':'password'})
    with TestClient(migrated) as owner, TestClient(migrated) as guest:
        owner.post('/api/dev/session', json={'username':'owner','password':'password'})
        guest.post('/api/guest/session')
        assert owner.get('/api/personal/status').json()['focus'] == 'legacy'
        assert guest.get('/api/guest/status').status_code == 404
