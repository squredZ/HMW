from fastapi.testclient import TestClient
from workbench.app import create_app


def test_shared_issue_upload_and_single_active_request(tmp_path):
    app = create_app(f'sqlite:///{tmp_path / "db.sqlite"}', dev_users={'owner':'password'}, storage_root=tmp_path / 'objects')
    with TestClient(app) as owner, TestClient(app) as first, TestClient(app) as second:
        csrf = {'X-CSRF-Token':owner.post('/api/dev/session', json={'username':'owner','password':'password'}).json()['csrf_token']}
        project = owner.post('/api/projects',headers=csrf,json={'name':'项目'}).json()['id']
        first.post('/api/guest/session'); second.post('/api/guest/session')
        created=first.post('/api/guest/issues',data={'project_id':project,'module':'通信','description':'启动失败'},files={'log':('problem.log',b'connection failed','text/plain')})
        assert created.status_code == 201, created.text
        issue_id=created.json()['id']
        item=second.get('/api/guest/issues').json()['items'][0]
        assert item['id'] == issue_id and item['module'] == '通信' and item['log_filename'] == 'problem.log'
        assert second.post(f'/api/guest/issues/{issue_id}/analyze').status_code == 202
        assert first.post(f'/api/guest/issues/{issue_id}/analyze').status_code == 409
        assert first.get('/api/guest/issues').json()['items'][0]['analysis']['state'] == 'queued'


def test_analysis_queue_serializes_workers_and_publishes_report(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from workbench.issues import analysis_worker

    root = tmp_path / 'objects'
    app = create_app(f'sqlite:///{tmp_path / "db.sqlite"}', dev_users={'owner':'password'}, storage_root=root)
    config = tmp_path / 'config.toml'
    config.write_text(f'[analysis]\ncode_root = "{tmp_path.as_posix()}"\n')
    entered, release = threading.Event(), threading.Event()
    class FakeAgent:
        def analyze(self, question, tools):
            assert '通信' in question
            entered.set()
            assert release.wait(5)
            return {'answer':'检查连接配置。','citations':[]}
    monkeypatch.setattr(analysis_worker, 'load_agent', lambda _: FakeAgent())
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf={'X-CSRF-Token':owner.post('/api/dev/session',json={'username':'owner','password':'password'}).json()['csrf_token']}
        project=owner.post('/api/projects',headers=csrf,json={'name':'项目'}).json()['id']
        guest.post('/api/guest/session')
        ids=[]
        for description in ('首次故障','第二次故障'):
            response=guest.post('/api/guest/issues',data={'project_id':project,'module':'通信','description':description},files={'log':('error.log',b'connection failed','text/plain')})
            ids.append(response.json()['id'])
            assert guest.post(f'/api/guest/issues/{ids[-1]}/analyze').status_code == 202
        with ThreadPoolExecutor(max_workers=2) as executor:
            future=executor.submit(analysis_worker.process_analysis_one,app.state.engine,root,config)
            assert entered.wait(5)
            try:
                assert executor.submit(analysis_worker.process_analysis_one,app.state.engine,root,config).result(timeout=5) is False
                states={item['id']:item['analysis']['state'] for item in guest.get('/api/guest/issues').json()['items']}
                assert sorted(states.values()) == ['queued','running']
            finally:
                release.set()
            assert future.result(timeout=5) is True
        assert analysis_worker.process_analysis_one(app.state.engine,root,config) is True
        assert all(item['analysis']['report'] == '检查连接配置。' for item in guest.get('/api/guest/issues').json()['items'])


def test_running_analysis_is_recovered_after_worker_stops(tmp_path, monkeypatch):
    import os
    import pytest
    if os.name == 'nt':
        pytest.skip('automatic recovery requires inherited POSIX lock')
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from workbench.issues.analysis_models import IssueAnalysis
    from workbench.issues import analysis_worker

    root=tmp_path / 'objects'
    app=create_app(f'sqlite:///{tmp_path / "db.sqlite"}', dev_users={'owner':'password'}, storage_root=root)
    config=tmp_path / 'config.toml'
    config.write_text(f'[analysis]\ncode_root="{tmp_path.as_posix()}"\n')
    class FakeAgent:
        def analyze(self, question, tools):
            return {'answer':'已恢复并完成。','citations':[]}
    monkeypatch.setattr(analysis_worker,'load_agent',lambda _:FakeAgent())
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf={'X-CSRF-Token':owner.post('/api/dev/session',json={'username':'owner','password':'password'}).json()['csrf_token']}
        project=owner.post('/api/projects',headers=csrf,json={'name':'项目'}).json()['id']
        guest.post('/api/guest/session')
        issue=guest.post('/api/guest/issues',data={'project_id':project,'module':'模块','description':'故障'},files={'log':('error.log',b'error','text/plain')}).json()['id']
        guest.post(f'/api/guest/issues/{issue}/analyze')
        with Session(app.state.engine) as db, db.begin():
            run=db.scalar(select(IssueAnalysis).where(IssueAnalysis.issue_id == issue))
            run.state='running'
        assert analysis_worker.process_analysis_one(app.state.engine,root,config)
        assert guest.get('/api/guest/issues').json()['items'][0]['analysis']['report'] == '已恢复并完成。'


def test_sqlite_lock_survives_worker_crash_while_child_runs(tmp_path):
    import os
    import subprocess
    import sys
    import time
    import pytest
    from sqlalchemy import create_engine
    from workbench.issues.analysis_worker import analysis_lock
    if os.name == 'nt':
        pytest.skip('POSIX descriptor inheritance')
    database = tmp_path / 'lock.sqlite'
    ready = tmp_path / 'child-ready'
    parent = '''
import os, subprocess, sys
from sqlalchemy import create_engine
from workbench.issues.analysis_worker import analysis_lock
engine = create_engine(sys.argv[1])
with analysis_lock(engine) as (acquired, fd):
    assert acquired
    child = "import pathlib,sys,time; pathlib.Path(sys.argv[1]).touch(); time.sleep(1.5)"
    subprocess.Popen([sys.executable, '-c', child, sys.argv[2]], pass_fds=(fd,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    while not os.path.exists(sys.argv[2]): pass
    os._exit(0)
'''
    url = f'sqlite:///{database}'
    result = subprocess.run([sys.executable, '-c', parent, url, str(ready)], timeout=5, check=False)
    assert result.returncode == 0
    engine = create_engine(url)
    try:
        with analysis_lock(engine) as (acquired, _):
            assert not acquired
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with analysis_lock(engine) as (acquired, _):
                if acquired:
                    break
            time.sleep(.05)
        assert acquired
    finally:
        engine.dispose()


def test_two_guests_cannot_enqueue_duplicate_analysis(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    app=create_app(f'sqlite:///{tmp_path / "db.sqlite"}',dev_users={'owner':'password'},storage_root=tmp_path/'objects')
    with TestClient(app) as owner, TestClient(app) as first, TestClient(app) as second:
        csrf={'X-CSRF-Token':owner.post('/api/dev/session',json={'username':'owner','password':'password'}).json()['csrf_token']}
        project=owner.post('/api/projects',headers=csrf,json={'name':'项目'}).json()['id']
        first.post('/api/guest/session'); second.post('/api/guest/session')
        issue=first.post('/api/guest/issues',data={'project_id':project,'module':'模块','description':'故障'},files={'log':('error.log',b'error','text/plain')}).json()['id']
        barrier=Barrier(2)
        def submit(client):
            barrier.wait(timeout=5)
            return client.post(f'/api/guest/issues/{issue}/analyze').status_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses=list(executor.map(submit,(first,second)))
        assert sorted(statuses)==[202,409]
