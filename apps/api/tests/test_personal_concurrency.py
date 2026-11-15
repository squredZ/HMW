from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from workbench.app import create_app


def test_simultaneous_edits_do_not_overwrite_each_other(tmp_path, monkeypatch):
    app = create_app(f"sqlite:///{tmp_path / 'concurrent.db'}", testing=True)
    headers = {'X-User-ID': 'owner', 'X-Principal-Type': 'human_session', 'X-CSRF-Token': 'test'}
    with TestClient(app) as client:
        initial = client.put('/api/personal/status', headers=headers, json={'focus': 'original'}).json()
        barrier = Barrier(2, timeout=5)
        original_scalar = Session.scalar

        def synchronize_reads(session, statement, *args, **kwargs):
            value = original_scalar(session, statement, *args, **kwargs)
            if 'personal_status.owner' in str(statement) and 'FOR UPDATE' in str(statement):
                barrier.wait()
            return value

        monkeypatch.setattr(Session, 'scalar', synchronize_reads)
        def edit(text):
            return client.put('/api/personal/status', headers=headers, json={'focus': text, 'expected_version': initial['version']})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(edit, ['first', 'second']))
        assert sorted(response.status_code for response in results) == [200, 409]
        stored = client.get('/api/personal/status', headers=headers).json()
        assert stored['version'] == initial['version'] + 1
        assert stored['focus'] in ('first', 'second')


def test_simultaneous_first_save_returns_conflict(tmp_path, monkeypatch):
    app = create_app(f"sqlite:///{tmp_path / 'create.db'}", testing=True)
    headers = {'X-User-ID': 'owner', 'X-Principal-Type': 'human_session', 'X-CSRF-Token': 'test'}
    barrier = Barrier(2, timeout=5)
    original_scalar = Session.scalar

    def synchronize_reads(session, statement, *args, **kwargs):
        value = original_scalar(session, statement, *args, **kwargs)
        if 'personal_status.owner' in str(statement) and 'FOR UPDATE' in str(statement):
            barrier.wait()
        return value

    monkeypatch.setattr(Session, 'scalar', synchronize_reads)
    with TestClient(app, raise_server_exceptions=False) as client:
        def save(text):
            return client.put('/api/personal/status', headers=headers, json={'focus': text})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, ['first', 'second']))
    assert sorted(response.status_code for response in results) == [200, 409]


def test_simultaneous_publish_consumes_one_version(tmp_path, monkeypatch):
    app = create_app(f"sqlite:///{tmp_path / 'publish.db'}", testing=True)
    headers = {'X-User-ID': 'owner', 'X-Principal-Type': 'human_session', 'X-CSRF-Token': 'test'}
    with TestClient(app) as client:
        draft = client.put('/api/personal/status', headers=headers, json={'focus':'draft'}).json()
        barrier = Barrier(2, timeout=5)
        original_scalar = Session.scalar

        def synchronize_reads(session, statement, *args, **kwargs):
            value = original_scalar(session, statement, *args, **kwargs)
            if 'personal_status.id' in str(statement) and 'FOR UPDATE' in str(statement):
                barrier.wait()
            return value

        monkeypatch.setattr(Session, 'scalar', synchronize_reads)
        def publish(_):
            return client.post(f"/api/personal/status/{draft['id']}/publish", headers=headers, json={'expected_version':draft['version']})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(publish, range(2)))
        assert sorted(response.status_code for response in results) == [200, 409]
