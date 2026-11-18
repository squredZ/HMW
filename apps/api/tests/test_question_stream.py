import asyncio
import json
import sys
import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
from workbench.app import create_app
from test_guest_questions import submit


def test_question_stream_reports_reads_and_preserves_answer_permissions(tmp_path):
    root = tmp_path / 'objects'
    app = create_app(f'sqlite:///{tmp_path / "db.sqlite"}', dev_users={'owner': 'password'}, storage_root=root)
    class Explorer:
        def explore(self, question, history, library):
            docs = library.list_documents()['documents']
            library.read_document(docs[0]['id'])
            return {'answer': '项目资料 [1]', 'citations': [1]}
    app.state.answer_agent = Explorer()
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf = {'X-CSRF-Token': owner.post('/api/dev/session', json={'username': 'owner', 'password': 'password'}).json()['csrf_token']}
        project = owner.post('/api/projects', headers=csrf, json={'name': '项目'}).json()['id']
        submit(owner, app, root, project, csrf, '私有项目资料', share=False)
        payload = {'project_id': project, 'question': '介绍项目'}
        assert guest.post('/api/guest/questions/stream', json=payload).status_code == 401
        guest.post('/api/guest/session')
        response = guest.post('/api/guest/questions/stream', json=payload)
        assert response.headers['content-type'].startswith('text/event-stream')
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        assert [event['stage'] for event in events if event['type'] == 'progress'] == ['thinking', 'reading', 'reading', 'answering']
        assert events[-1]['data']['citations'] == [1]
        assert events[-1]['data']['evidence'][0]['guest_visible'] is False
        assert '私有项目资料' not in str(events[:-1])


def test_disconnected_question_cancels_agent_process():
    from workbench.questions.agent import AgentError, run_tool_process

    cancelled = threading.Event()
    library = SimpleNamespace(cancelled=cancelled, evidence=[])
    errors = []
    def run():
        try:
            run_tool_process([sys.executable, '-c', 'import time; time.sleep(30)'],
                             {'settings':{}}, {}, library, timeout=10)
        except AgentError as exc:
            errors.append(exc.reason)
    worker = threading.Thread(target=run)
    worker.start()
    time.sleep(0.1)
    cancelled.set()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert errors == ['cancelled']


def test_stream_delivers_progress_before_agent_finishes(tmp_path):
    app = create_app(f'sqlite:///{tmp_path / "db.sqlite"}', dev_users={'owner':'password'}, storage_root=tmp_path / 'objects')
    release = threading.Event()
    class SlowExplorer:
        def explore(self, question, history, library):
            assert release.wait(5)
            return {'answer':'你好', 'citations':[]}
    app.state.answer_agent = SlowExplorer()
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf = {'X-CSRF-Token':owner.post('/api/dev/session', json={'username':'owner','password':'password'}).json()['csrf_token']}
        project = owner.post('/api/projects', headers=csrf, json={'name':'项目'}).json()['id']
        guest.post('/api/guest/session')
        cookie = guest.cookies.get('hmw_guest')

        async def exercise():
            body = json.dumps({'project_id':project,'question':'你好'}).encode()
            received = False
            saw_progress = asyncio.Event()
            sent = []
            async def receive():
                nonlocal received
                if not received:
                    received = True
                    return {'type':'http.request','body':body,'more_body':False}
                await asyncio.Future()
            async def send(message):
                sent.append(message)
                if b'"stage": "thinking"' in message.get('body', b''):
                    saw_progress.set()
            scope = {'type':'http','asgi':{'version':'3.0','spec_version':'2.3'}, 'http_version':'1.1',
                     'method':'POST','scheme':'http','path':'/api/guest/questions/stream',
                     'raw_path':b'/api/guest/questions/stream','query_string':b'',
                     'headers':[(b'content-type',b'application/json'),(b'cookie',f'hmw_guest={cookie}'.encode())],
                     'client':('127.0.0.1',1234),'server':('test',80),'root_path':''}
            task = asyncio.create_task(app(scope, receive, send))
            try:
                await asyncio.wait_for(saw_progress.wait(), timeout=3)
                assert not release.is_set()
            finally:
                release.set()
            await asyncio.wait_for(task, timeout=5)
            assert any(b'"type": "result"' in message.get('body', b'') for message in sent)
        asyncio.run(exercise())
