from datetime import datetime, timezone
from fastapi.testclient import TestClient
from workbench.app import create_app
from workbench.jobs.worker import process_one


def h(user):
    return {'X-User-ID': user, 'X-Principal-Type':'human_session', 'X-CSRF-Token':'test'}


def test_import_job_persists_draft_and_requires_authorized_publication(tmp_path):
    database = f"sqlite:///{tmp_path / 'db.sqlite'}"
    root = tmp_path / 'objects'
    app = create_app(database, testing=True, storage_root=root)
    with TestClient(app) as client:
        pid = client.post('/api/projects', headers=h('owner'), json={'name':'A'}).json()['id']
        client.put(f'/api/projects/{pid}/members/member', headers=h('owner'), json={'role':'member'})
        response = client.post(f'/api/projects/{pid}/imports', headers=h('owner'), files=[('files', ('source.md', b'# Title\nEvidence\n', 'text/markdown'))])
        assert response.status_code == 202
        job_id = response.json()['jobs'][0]
        assert client.get(f'/api/imports/{job_id}', headers=h('outsider')).status_code == 404
        assert client.get(f'/api/imports/{job_id}', headers=h('member')).status_code == 404
        assert client.get(f'/api/imports/{job_id}', headers=h('owner')).json()['state'] == 'queued'
    assert process_one(app.state.engine, root, 'test-worker', datetime.now(timezone.utc))
    restarted = create_app(database, testing=True, storage_root=root)
    with TestClient(restarted) as client:
        job = client.get(f'/api/imports/{job_id}', headers=h('owner')).json()
        assert job['state'] == 'completed'
        entry = job['result']['knowledge_id']
        draft = client.get(f'/api/knowledge/{entry}', headers=h('owner')).json()
        assert draft['state'] == 'draft'
        assert draft['content'] == '# Title\nEvidence\n'
        assert draft['locator'] == {'start_line':1,'end_line':2}
        assert client.get(f'/api/knowledge/{entry}', headers=h('member')).status_code == 404
        assert client.post('/api/knowledge/publish-batch', headers=h('member'), json={'version_ids':[entry]}).status_code == 404
        assert client.post('/api/knowledge/publish-batch', headers=h('owner'), json={'version_ids':[entry]}).status_code == 200
        assert client.get(f'/api/knowledge/{entry}', headers=h('member')).json()['content'] == draft['content']
        assert client.get(f'/api/knowledge/{entry}', headers=h('outsider')).status_code == 404


def test_unsupported_or_invalid_text_never_silently_publishes(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", testing=True, storage_root=tmp_path / 'objects')
    with TestClient(app) as client:
        pid = client.post('/api/projects', headers=h('owner'), json={'name':'A'}).json()['id']
        bad = client.post(f'/api/projects/{pid}/imports', headers=h('owner'), files=[('files',('bad.exe',b'data','application/octet-stream'))])
        assert bad.status_code == 422
        response = client.post(f'/api/projects/{pid}/imports', headers=h('owner'), files=[('files',('unknown.txt',b'\xff\xfe\xff','text/plain'))])
        job_id = response.json()['jobs'][0]
        assert process_one(app.state.engine, tmp_path / 'objects', 'worker', datetime.now(timezone.utc))
        job = client.get(f'/api/imports/{job_id}', headers=h('owner')).json()
        assert job['state'] == 'needs_input'
        assert job['result']['reason'] == 'encoding_unrecognized'


def test_revoked_maintainer_does_not_generate_draft(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", testing=True, storage_root=tmp_path / 'objects')
    with TestClient(app) as client:
        pid = client.post('/api/projects', headers=h('owner'), json={'name':'A'}).json()['id']
        client.put(f'/api/projects/{pid}/members/maintainer', headers=h('owner'), json={'role':'maintainer'})
        response = client.post(f'/api/projects/{pid}/imports', headers=h('maintainer'), files=[('files',('text.txt',b'content','text/plain'))])
        job_id = response.json()['jobs'][0]
        client.delete(f'/api/projects/{pid}/members/maintainer', headers=h('owner'))
        assert process_one(app.state.engine, tmp_path / 'objects', 'worker', datetime.now(timezone.utc))
        job = client.get(f'/api/imports/{job_id}', headers=h('owner')).json()
        assert job['state'] == 'failed'
        assert job['result']['reason'] == 'permission_revoked'


def test_invalid_batch_removes_staged_file_and_creates_no_job(tmp_path):
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", testing=True, storage_root=root)
    with TestClient(app) as client:
        pid = client.post('/api/projects', headers=h('owner'), json={'name':'A'}).json()['id']
        response = client.post(f'/api/projects/{pid}/imports', headers=h('owner'), files=[('files',('ok.txt',b'safe','text/plain')),('files',('bad.exe',b'bad','application/octet-stream'))])
        assert response.status_code == 422
        assert list(root.iterdir()) == []
        assert not process_one(app.state.engine, root, 'worker', datetime.now(timezone.utc))
        too_many = [('files',(f'{i}.txt',b'a','text/plain')) for i in range(21)]
        assert client.post(f'/api/projects/{pid}/imports', headers=h('owner'), files=too_many).status_code == 413


def test_worker_processes_job_in_separate_process(tmp_path):
    import os
    import subprocess
    import sys
    database = f"sqlite:///{tmp_path / 'db.sqlite'}"
    root = tmp_path / 'objects'
    app = create_app(database, testing=True, storage_root=root)
    with TestClient(app) as client:
        pid = client.post('/api/projects', headers=h('owner'), json={'name':'A'}).json()['id']
        job = client.post(f'/api/projects/{pid}/imports', headers=h('owner'), files=[('files',('a.txt',b'line one\nline two','text/plain'))]).json()['jobs'][0]
        env = dict(os.environ, PYTHONPATH=str(__import__('pathlib').Path(__file__).parents[1]))
        result = subprocess.run([sys.executable, '-c', 'import sys; from pathlib import Path; from sqlalchemy import create_engine; from workbench.jobs.worker import process_one; process_one(create_engine(sys.argv[1]), Path(sys.argv[2]), "subprocess")', database, str(root)], env=env, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        assert client.get(f'/api/imports/{job}', headers=h('owner')).json()['state'] == 'completed'


def test_revocation_during_file_read_prevents_draft(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    from threading import Event
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", testing=True, storage_root=root)
    with TestClient(app) as client:
        pid = client.post('/api/projects', headers=h('owner'), json={'name':'A'}).json()['id']
        client.put(f'/api/projects/{pid}/members/maintainer', headers=h('owner'), json={'role':'maintainer'})
        job_id = client.post(f'/api/projects/{pid}/imports', headers=h('maintainer'), files=[('files',('a.txt',b'private','text/plain'))]).json()['jobs'][0]
        entered, resume = Event(), Event()
        original = Path.open
        def paused_open(path, *args, **kwargs):
            if path.parent == root and args and args[0] == 'rb':
                entered.set()
                assert resume.wait(timeout=5)
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'open', paused_open)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(process_one, app.state.engine, root, 'worker')
            assert entered.wait(timeout=5)
            assert client.delete(f'/api/projects/{pid}/members/maintainer', headers=h('owner')).status_code == 204
            resume.set()
            assert pending.result(timeout=5)
        result = client.get(f'/api/imports/{job_id}', headers=h('owner')).json()
        assert result['state'] == 'failed'
        assert result['result']['reason'] == 'permission_revoked'


def test_batch_publication_rolls_back_if_any_version_is_invalid(tmp_path):
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", testing=True, storage_root=root)
    with TestClient(app) as client:
        pid = client.post('/api/projects', headers=h('owner'), json={'name':'A'}).json()['id']
        response = client.post(f'/api/projects/{pid}/imports', headers=h('owner'), files=[('files',('a.txt',b'content','text/plain'))])
        job = response.json()['jobs'][0]
        process_one(app.state.engine, root, 'worker', datetime.now(timezone.utc))
        version = client.get(f'/api/imports/{job}', headers=h('owner')).json()['result']['knowledge_id']
        failed = client.post('/api/knowledge/publish-batch', headers=h('owner'), json={'version_ids':[version,'missing']})
        assert failed.status_code == 404
        assert client.get(f'/api/knowledge/{version}', headers=h('owner')).json()['state'] == 'draft'


def test_modified_source_is_rejected_before_draft_creation(tmp_path):
    from pathlib import Path
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", testing=True, storage_root=root)
    with TestClient(app) as client:
        pid = client.post('/api/projects', headers=h('owner'), json={'name':'A'}).json()['id']
        job = client.post(f'/api/projects/{pid}/imports', headers=h('owner'), files=[('files',('a.txt',b'original','text/plain'))]).json()['jobs'][0]
        path = next(Path(root).iterdir())
        path.write_bytes(b'tampered')
        assert process_one(app.state.engine, root, 'worker')
        result = client.get(f'/api/imports/{job}', headers=h('owner')).json()
        assert result['state'] == 'failed'
        assert result['result']['reason'] == 'source_integrity_failed'


def test_configurable_file_size_limit_cleans_staged_objects(tmp_path):
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", testing=True, storage_root=root, max_file_bytes=4)
    with TestClient(app) as client:
        pid = client.post('/api/projects', headers=h('owner'), json={'name':'A'}).json()['id']
        response = client.post(f'/api/projects/{pid}/imports', headers=h('owner'), files=[('files',('big.txt',b'12345','text/plain'))])
        assert response.status_code == 413
        assert list(root.iterdir()) == []
        assert not process_one(app.state.engine, root, 'worker')


def test_worker_bounds_source_read_even_if_object_grows(tmp_path):
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", testing=True, storage_root=root)
    with TestClient(app) as client:
        pid = client.post('/api/projects', headers=h('owner'), json={'name':'A'}).json()['id']
        job = client.post(f'/api/projects/{pid}/imports', headers=h('owner'), files=[('files',('a.txt',b'original','text/plain'))]).json()['jobs'][0]
        assert process_one(app.state.engine, root, 'worker', max_source_bytes=4)
        result = client.get(f'/api/imports/{job}', headers=h('owner')).json()
        assert result['state'] == 'failed'
        assert result['result']['reason'] == 'source_too_large'


def test_oversized_streaming_file_is_rejected(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", testing=True, storage_root=tmp_path / 'objects', max_file_bytes=4, max_batch_files=1)
    with TestClient(app) as client:
        pid = client.post('/api/projects', headers=h('owner'), json={'name':'A'}).json()['id']
        body = b'--BOUNDARY\r\nContent-Disposition: form-data; name="files"; filename="a.txt"\r\nContent-Type: text/plain\r\n\r\n' + b'a' * (1024 * 1024 + 8) + b'\r\n--BOUNDARY--\r\n'
        headers = {**h('owner'), 'Content-Type':'multipart/form-data; boundary=BOUNDARY'}
        response = client.post(f'/api/projects/{pid}/imports', headers=headers, content=iter([body[:100], body[100:]]))
        assert response.status_code == 413


def test_declared_oversized_request_is_rejected_before_multipart_parsing(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", testing=True, storage_root=tmp_path / 'objects', max_file_bytes=4, max_batch_files=1)
    with TestClient(app) as client:
        pid = client.post('/api/projects', headers=h('owner'), json={'name':'A'}).json()['id']
        headers = {**h('owner'), 'Content-Length':str(2 * 1024 * 1024)}
        response = client.post(f'/api/projects/{pid}/imports', headers=headers, files=[('files',('a.txt',b'1234','text/plain'))])
        assert response.status_code == 413
        assert not (tmp_path / 'objects').exists()


def test_source_download_follows_publication_and_current_project_membership(tmp_path):
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", testing=True, storage_root=root)
    with TestClient(app) as client:
        pid = client.post('/api/projects', headers=h('owner'), json={'name':'A'}).json()['id']
        client.put(f'/api/projects/{pid}/members/member', headers=h('owner'), json={'role':'member'})
        job = client.post(f'/api/projects/{pid}/imports', headers=h('owner'), files=[('files',('a.txt',b'original','text/plain'))]).json()['jobs'][0]
        process_one(app.state.engine, root, 'worker')
        version_id = client.get(f'/api/imports/{job}', headers=h('owner')).json()['result']['knowledge_id']
        source_id = client.get(f'/api/knowledge/{version_id}', headers=h('owner')).json()['source']['id']
        path = f'/api/sources/{source_id}/download'
        assert client.get(path, headers=h('member')).status_code == 404
        assert client.get(path, headers=h('owner')).content == b'original'
        client.post('/api/knowledge/publish-batch', headers=h('owner'), json={'version_ids':[version_id]})
        assert client.get(path, headers=h('member')).content == b'original'
        client.delete(f'/api/projects/{pid}/members/member', headers=h('owner'))
        assert client.get(path, headers=h('member')).status_code == 404
        assert client.get(f'/api/knowledge/{version_id}', headers=h('member')).status_code == 404


def test_worker_logs_job_result_without_file_content(tmp_path, caplog):
    import logging
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", testing=True, storage_root=root)
    with TestClient(app) as client:
        pid = client.post('/api/projects', headers=h('owner'), json={'name':'A'}).json()['id']
        response = client.post(f'/api/projects/{pid}/imports', headers=h('owner'), files=[('files',('a.txt',b'private content','text/plain'))])
        job = response.json()['jobs'][0]
        with caplog.at_level(logging.INFO, logger='workbench.worker'):
            process_one(app.state.engine, root, 'worker')
    import json
    events = [json.loads(record.message) for record in caplog.records if record.name == 'workbench.worker']
    assert [event['event'] for event in events] == ['import_started', 'import_finished']
    assert all(event['request_id'] == response.headers['X-Request-ID'] and event['lease_token'] == 1 for event in events)
    assert job in caplog.text
    assert 'completed' in caplog.text
    assert 'private content' not in caplog.text


def test_slow_import_renews_lease_while_reading(tmp_path):
    from datetime import timedelta
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", testing=True, storage_root=root)
    with TestClient(app) as client:
        pid = client.post('/api/projects', headers=h('owner'), json={'name':'A'}).json()['id']
        job = client.post(f'/api/projects/{pid}/imports', headers=h('owner'), files=[('files',('a.txt',b'0123456789','text/plain'))]).json()['jobs'][0]
        clock_now = datetime.now(timezone.utc)
        def clock():
            nonlocal clock_now
            clock_now += timedelta(seconds=16)
            return clock_now
        assert process_one(app.state.engine, root, 'worker', now=clock_now, clock=clock, chunk_bytes=2)
        result = client.get(f'/api/imports/{job}', headers=h('owner')).json()
        assert result['state'] == 'completed'


def test_worker_losing_lease_during_read_cannot_publish_result(tmp_path):
    from datetime import timedelta
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", testing=True, storage_root=root)
    with TestClient(app) as client:
        pid = client.post('/api/projects', headers=h('owner'), json={'name':'A'}).json()['id']
        job = client.post(f'/api/projects/{pid}/imports', headers=h('owner'), files=[('files',('a.txt',b'content','text/plain'))]).json()['jobs'][0]
        now = datetime.now(timezone.utc)
        assert process_one(app.state.engine, root, 'slow-worker', now=now, clock=lambda: now + timedelta(seconds=61), chunk_bytes=2)
        assert client.get(f'/api/imports/{job}', headers=h('owner')).json()['state'] == 'running'
        assert process_one(app.state.engine, root, 'recovery-worker', now=now + timedelta(seconds=61), clock=lambda: now + timedelta(seconds=61))
        result = client.get(f'/api/imports/{job}', headers=h('owner')).json()
        assert result['state'] == 'completed'


def test_streaming_upload_stops_reading_when_request_budget_is_exceeded(tmp_path):
    import asyncio
    import httpx

    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", testing=True,
                     storage_root=tmp_path / 'objects', max_file_bytes=4, max_batch_files=1)
    with TestClient(app) as client:
        pid = client.post('/api/projects', headers=h('owner'), json={'name':'A'}).json()['id']
    consumed = []

    class Upload(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'--X\r\nContent-Disposition: form-data; name="files"; filename="a.txt"\r\nContent-Type: text/plain\r\n\r\n'
            for index in range(10):
                consumed.append(index)
                yield b'a' * (512 * 1024)
            yield b'\r\n--X--\r\n'

    async def send():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            return await client.post(f'/api/projects/{pid}/imports', headers={**h('owner'), 'Content-Type':'multipart/form-data; boundary=X'}, content=Upload())
    response = asyncio.run(send())
    assert response.status_code == 413
    assert len(consumed) < 10
    assert not (tmp_path / 'objects').exists()


def test_import_history_is_project_scoped_and_available_after_restart(tmp_path):
    database = f"sqlite:///{tmp_path / 'db.sqlite'}"
    root = tmp_path / 'objects'
    app = create_app(database, testing=True, storage_root=root)
    with TestClient(app) as client:
        a = client.post('/api/projects', headers=h('owner'), json={'name':'A'}).json()['id']
        b = client.post('/api/projects', headers=h('owner'), json={'name':'B'}).json()['id']
        client.put(f'/api/projects/{a}/members/member', headers=h('owner'), json={'role':'member'})
        a_job = client.post(f'/api/projects/{a}/imports', headers=h('owner'), files=[('files',('a.txt',b'a','text/plain'))]).json()['jobs'][0]
        b_job = client.post(f'/api/projects/{b}/imports', headers=h('owner'), files=[('files',('b.txt',b'b','text/plain'))]).json()['jobs'][0]
    reopened = create_app(database, testing=True, storage_root=root)
    with TestClient(reopened) as client:
        items = client.get(f'/api/projects/{a}/imports', headers=h('owner')).json()['jobs']
        assert [item['id'] for item in items] == [a_job]
        assert b_job not in str(items)
        assert client.get(f'/api/projects/{a}/imports', headers=h('member')).status_code == 404
        assert client.get(f'/api/projects/{a}/imports', headers=h('stranger')).status_code == 404


def test_stream_limit_closes_multipart_temporary_files(tmp_path, monkeypatch):
    import asyncio
    import httpx
    import starlette.formparsers

    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", testing=True,
                     storage_root=tmp_path / 'objects', max_file_bytes=4, max_batch_files=1)
    with TestClient(app) as client:
        pid = client.post('/api/projects', headers=h('owner'), json={'name':'A'}).json()['id']
    created = []
    factory = starlette.formparsers.SpooledTemporaryFile
    def tracked_file(*args, **kwargs):
        file = factory(*args, **kwargs)
        created.append(file)
        return file
    monkeypatch.setattr(starlette.formparsers, 'SpooledTemporaryFile', tracked_file)

    class Upload(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'--X\r\nContent-Disposition: form-data; name="files"; filename="a.txt"\r\nContent-Type: text/plain\r\n\r\n'
            for _ in range(5):
                yield b'a' * (512 * 1024)
            yield b'\r\n--X--\r\n'

    async def send():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            return await client.post(f'/api/projects/{pid}/imports', headers={**h('owner'), 'Content-Type':'multipart/form-data; boundary=X'}, content=Upload())
    assert asyncio.run(send()).status_code == 413
    assert created and all(file.closed for file in created)


def test_non_multipart_upload_is_rejected_without_reading_body(tmp_path):
    import asyncio
    import httpx
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", testing=True,
                     storage_root=tmp_path / 'objects', max_file_bytes=4, max_batch_files=1)
    with TestClient(app) as client:
        pid = client.post('/api/projects', headers=h('owner'), json={'name':'A'}).json()['id']
    consumed = []
    class Upload(httpx.AsyncByteStream):
        async def __aiter__(self):
            for index in range(10):
                consumed.append(index)
                yield b'x' * (512 * 1024)
    async def send():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            return await client.post(f'/api/projects/{pid}/imports', headers={**h('owner'), 'Content-Type':'application/octet-stream'}, content=Upload())
    response = asyncio.run(send())
    assert response.status_code == 422
    assert consumed == []
