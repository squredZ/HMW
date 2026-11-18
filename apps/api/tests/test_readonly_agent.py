from fastapi.testclient import TestClient
from workbench.app import create_app
from test_guest_questions import submit


def test_agent_receives_published_knowledge_regardless_of_guest_visibility(tmp_path):
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner':'password'}, storage_root=root)
    calls = []
    class FakeAgent:
        def answer(self, question, evidence):
            calls.append((question, evidence))
            return {'answer':'版本是 V2 [1]。', 'citations':[1]}
    app.state.answer_agent = FakeAgent()
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf = {'X-CSRF-Token':owner.post('/api/dev/session',json={'username':'owner','password':'password'}).json()['csrf_token']}
        pid = owner.post('/api/projects',headers=csrf,json={'name':'A'}).json()['id']
        submit(owner, app, root, pid, csrf, '接口版本是 V2。', share=True)
        submit(owner, app, root, pid, csrf, '接口私密内容不应传出。', share=False)
        guest.post('/api/guest/session')
        data = guest.post('/api/guest/questions',json={'project_id':pid,'question':'接口版本'}).json()
        assert data['kind'] == 'generated' and data['message'] == '版本是 V2 [1]。'
        assert len(calls) == 1 and len(calls[0][1]) == 2
        assert {item['guest_visible'] for item in calls[0][1]} == {True, False}
        assert any(item['excerpt'] == '接口版本是 V2。' for item in data['evidence'])


def test_guest_download_revocation_does_not_block_published_project_qa(tmp_path):
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner':'password'}, storage_root=root)
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf = {'X-CSRF-Token':owner.post('/api/dev/session',json={'username':'owner','password':'password'}).json()['csrf_token']}
        pid = owner.post('/api/projects',headers=csrf,json={'name':'A'}).json()['id']
        version = submit(owner, app, root, pid, csrf, '接口版本是 V2。', share=True)
        class RevokingAgent:
            def answer(self, question, evidence):
                owner.put(f'/api/knowledge/{version}/guest-visibility',headers=csrf,json={'visible':False})
                return {'answer':'版本是 V2 [1]。', 'citations':[1]}
        app.state.answer_agent = RevokingAgent()
        guest.post('/api/guest/session')
        response = guest.post('/api/guest/questions',json={'project_id':pid,'question':'接口版本'})
        assert response.status_code == 200
        assert response.json()['kind'] == 'generated'
        assert response.json()['evidence'][0]['guest_visible'] is False


def test_agent_failure_after_guest_download_revocation_keeps_published_qa(tmp_path):
    from workbench.questions.agent import AgentError
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner':'password'}, storage_root=root)
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf = {'X-CSRF-Token':owner.post('/api/dev/session',json={'username':'owner','password':'password'}).json()['csrf_token']}
        pid = owner.post('/api/projects',headers=csrf,json={'name':'A'}).json()['id']
        version = submit(owner, app, root, pid, csrf, '接口版本是 V2。', share=True)
        class FailingAgent:
            def answer(self, question, evidence):
                owner.put(f'/api/knowledge/{version}/guest-visibility',headers=csrf,json={'visible':False})
                raise AgentError('timeout')
        app.state.answer_agent = FailingAgent()
        guest.post('/api/guest/session')
        response = guest.post('/api/guest/questions',json={'project_id':pid,'question':'接口版本'})
        assert response.status_code == 200
        assert response.json()['agent_unavailable'] is True
        assert response.json()['evidence'][0]['guest_visible'] is False


def test_model_config_is_explicit_and_validated(tmp_path):
    import pytest
    from workbench.questions.agent import load_agent
    path = tmp_path / 'config.toml'
    path.write_text('[llm]\nenabled=false\napi_key="unused"\n')
    assert load_agent(path) is None
    path.write_text('[llm]\nenabled=true\napi_key="key"\nmodel="deepseek-chat"\napi_url="http://example.com"\n')
    with pytest.raises(ValueError):
        load_agent(path)
    path.write_text('[llm]\nenabled=true\napi_key="key"\nmodel="deepseek-chat"\napi_url="https://api.deepseek.com"\nreasoning_effort="off"\n')
    assert load_agent(path) is not None


def test_invalid_model_citations_fall_back_to_verified_evidence(tmp_path):
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner':'password'}, storage_root=root)
    class InvalidAgent:
        def answer(self, question, evidence):
            return {'answer':'虚构的回答 [99]', 'citations':[99]}
    app.state.answer_agent = InvalidAgent()
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf = {'X-CSRF-Token':owner.post('/api/dev/session',json={'username':'owner','password':'password'}).json()['csrf_token']}
        pid = owner.post('/api/projects',headers=csrf,json={'name':'A'}).json()['id']
        submit(owner, app, root, pid, csrf, '接口版本是 V2。', share=True)
        guest.post('/api/guest/session')
        response = guest.post('/api/guest/questions',json={'project_id':pid,'question':'接口版本'}).json()
        assert response['kind'] == 'extractive' and response['agent_unavailable'] is True
        assert '虚构' not in str(response)


def test_no_evidence_never_calls_agent(tmp_path, monkeypatch):
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner':'password'}, storage_root=root)
    class ForbiddenAgent:
        def answer(self, question, evidence):
            raise AssertionError('agent must not run without evidence')
    app.state.answer_agent = ForbiddenAgent()
    monkeypatch.setattr('workbench.questions.retrieval._source_valid', lambda *args: False)
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf = {'X-CSRF-Token':owner.post('/api/dev/session',json={'username':'owner','password':'password'}).json()['csrf_token']}
        pid = owner.post('/api/projects',headers=csrf,json={'name':'A'}).json()['id']
        submit(owner, app, root, pid, csrf, '这是产品规范。', share=True)
        guest.post('/api/guest/session')
        result = guest.post('/api/guest/questions',json={'project_id':pid,'question':'发动机散热'}).json()
        assert result['kind'] == 'insufficient_evidence'


def test_pi_process_does_not_inherit_owner_or_config_secrets(tmp_path, monkeypatch):
    import json
    from workbench.questions.agent import ReadonlyPiAgent
    monkeypatch.setenv('HMW_DEV_PASSWORD', 'owner-secret')
    monkeypatch.setenv('HMW_CONFIG_PATH', '/private/config')
    monkeypatch.setenv('HMW_DATABASE_URL', 'private-database')
    monkeypatch.setattr('workbench.questions.agent.shutil.which', lambda name: '/test/node')
    def run(command, payload, env, **kwargs):
        assert 'HMW_DEV_PASSWORD' not in env
        assert 'HMW_CONFIG_PATH' not in env
        assert 'HMW_DATABASE_URL' not in env
        assert 'model-secret' not in str(command)
        assert json.loads(payload)['settings']['api_key'] == 'model-secret'
        return 0, '{"answer":"答案 [1]","citations":[1]}'
    monkeypatch.setattr('workbench.questions.agent.run_bounded_process', run)
    agent = ReadonlyPiAgent({'api_key':'model-secret','api_url':'https://example.com','model':'model'}, tmp_path / 'agent.mjs')
    assert agent.answer('问题', [{'excerpt':'答案'}])['citations'] == [1]


def test_agent_limits_concurrency_and_releases_slots(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    import pytest
    from workbench.questions.agent import AgentError, ReadonlyPiAgent
    started = threading.Barrier(3)
    release = threading.Event()
    monkeypatch.setattr('workbench.questions.agent.shutil.which', lambda name: '/test/node')
    def run(*args, **kwargs):
        started.wait(timeout=3)
        assert release.wait(timeout=3)
        return 0, '{"answer":"答案 [1]","citations":[1]}'
    monkeypatch.setattr('workbench.questions.agent.run_bounded_process', run)
    agent = ReadonlyPiAgent({'api_key':'key','api_url':'https://example.com','model':'model'}, tmp_path / 'agent.mjs')
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(agent.answer, '问题', [{'excerpt':'答案'}]) for _ in range(2)]
        started.wait(timeout=3)
        try:
            with pytest.raises(AgentError, match='busy'):
                agent.answer('问题', [{'excerpt':'答案'}])
        finally:
            release.set()
        assert all(f.result(timeout=3)['citations'] == [1] for f in futures)
    monkeypatch.setattr('workbench.questions.agent.run_bounded_process', lambda *a, **k: (0, '{"answer":"答案 [1]","citations":[1]}'))
    assert agent.answer('问题', [{'excerpt':'答案'}])['citations'] == [1]


def test_child_output_is_bounded_and_utf8_is_preserved():
    import os
    import sys
    import pytest
    from workbench.questions.agent import AgentError, run_bounded_process
    code, text = run_bounded_process([sys.executable, '-c', 'import sys;sys.stdout.buffer.write("中文".encode("utf-8"))'], b'', dict(os.environ), timeout=3)
    assert code == 0 and text == '中文'
    with pytest.raises(AgentError, match='output_limit'):
        run_bounded_process([sys.executable, '-c', 'import sys;sys.stdout.buffer.write(b"x"*1000000)'], b'', dict(os.environ), timeout=3)


def test_child_timeout_terminates_and_releases_resources():
    import os
    import sys
    import pytest
    from workbench.questions.agent import AgentError, run_bounded_process
    with pytest.raises(AgentError, match='timeout'):
        run_bounded_process([sys.executable, '-c', 'import time;time.sleep(10)'], b'', dict(os.environ), timeout=0.1)


def test_agent_can_assess_authorized_context_when_keywords_do_not_match(tmp_path):
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner':'password'}, storage_root=root)
    calls = []
    class FakeAgent:
        def answer(self, question, evidence):
            calls.append(evidence)
            return {'answer':'资料介绍了项目的背景 [1]。', 'citations':[1]}
    app.state.answer_agent = FakeAgent()
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf = {'X-CSRF-Token':owner.post('/api/dev/session',json={'username':'owner','password':'password'}).json()['csrf_token']}
        project = owner.post('/api/projects',headers=csrf,json={'name':'A'}).json()['id']
        submit(owner, app, root, project, csrf, '项目背景：提供可靠的数据同步服务。', share=True)
        submit(owner, app, root, project, csrf, '绝密资料仅本人可见。', share=False)
        guest.post('/api/guest/session')
        result = guest.post('/api/guest/questions',json={'project_id':project,'question':'请简单介绍一下'}).json()
        assert result['kind'] == 'generated' and len(calls) == 1
        assert result['retrieval_mode'] == 'context'
        assert any(item['guest_visible'] is False for item in calls[0])


def test_model_can_abstain_when_evidence_does_not_answer_question(tmp_path):
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner':'password'}, storage_root=root)
    calls = []
    class AbstainingAgent:
        def answer(self, question, evidence):
            calls.append(evidence)
            return {'answer':'没有足够的信息。', 'citations':[]}
    app.state.answer_agent = AbstainingAgent()
    with TestClient(app) as owner, TestClient(app) as guest:
        csrf = {'X-CSRF-Token':owner.post('/api/dev/session',json={'username':'owner','password':'password'}).json()['csrf_token']}
        project = owner.post('/api/projects',headers=csrf,json={'name':'A'}).json()['id']
        submit(owner, app, root, project, csrf, '这是产品规范。', share=True)
        guest.post('/api/guest/session')
        result = guest.post('/api/guest/questions',json={'project_id':project,'question':'发动机散热'}).json()
        assert result['kind'] == 'insufficient_evidence' and result['agent_invoked'] is True and len(calls) == 1
        assert 'agent_unavailable' not in result
        assert result['evidence'] == []


def test_model_budgets_are_validated_and_forwarded(tmp_path, monkeypatch):
    import json
    import pytest
    from workbench.questions.agent import ReadonlyPiAgent
    settings = {'api_key':'key','api_url':'https://example.com','model':'deepseek-v4-pro',
                'max_output_tokens':8192,'context_window_tokens':1048576,
                'max_context_bytes':150000,'max_answer_chars':12000,'timeout_seconds':180}
    agent = ReadonlyPiAgent(settings, tmp_path / 'readonly.mjs')
    monkeypatch.setattr('workbench.questions.agent.shutil.which', lambda _: '/test/node')
    def run(command, payload, env, **kwargs):
        configured = json.loads(payload)['settings']
        assert kwargs['timeout'] == 180
        assert configured['max_output_tokens'] == 8192
        assert configured['context_window_tokens'] == 1048576
        assert configured['max_context_bytes'] == 150000
        assert configured['max_answer_chars'] == 12000
        return 0, json.dumps({'answer':'答' * 5000 + ' [1]','citations':[1]}, ensure_ascii=False)
    monkeypatch.setattr('workbench.questions.agent.run_bounded_process', run)
    assert len(agent.answer('问题',[{'excerpt':'资料'}])['answer']) > 4000
    for key, value in [('max_output_tokens',0),('max_context_bytes',True),('max_answer_chars',-1),('timeout_seconds',0)]:
        with pytest.raises(ValueError):
            ReadonlyPiAgent({**settings,key:value}, tmp_path / 'agent.mjs')
    with pytest.raises(ValueError):
        ReadonlyPiAgent({**settings,'max_output_tokens':8192,'context_window_tokens':8192}, tmp_path / 'agent.mjs')


def test_agent_proxy_configuration_is_explicit_and_does_not_leak_other_environment(tmp_path, monkeypatch):
    import pytest
    from workbench.questions.agent import ReadonlyPiAgent
    monkeypatch.setenv('HTTPS_PROXY','http://company.example:3128')
    monkeypatch.setenv('HMW_DEV_PASSWORD','owner-secret')
    monkeypatch.setattr('workbench.questions.agent.shutil.which',lambda _: '/test/node')
    observed=[]
    def run(command, payload, env, **kwargs):
        observed.append(env)
        return 0, '{"answer":"答案 [1]","citations":[1]}'
    monkeypatch.setattr('workbench.questions.agent.run_bounded_process',run)
    base={'api_key':'key','api_url':'https://example.com','model':'deepseek-v4-pro'}
    ReadonlyPiAgent(base,tmp_path / 'agent.mjs').answer('问题',[{'excerpt':'资料'}])
    assert 'HTTPS_PROXY' not in observed[-1]
    proxy=ReadonlyPiAgent(base,tmp_path / 'agent.mjs',network={'proxy_mode':'environment','no_proxy':'localhost,127.0.0.1'})
    proxy.answer('问题',[{'excerpt':'资料'}])
    assert observed[-1]['HTTPS_PROXY'] == 'http://company.example:3128'
    assert observed[-1]['NO_PROXY'] == 'localhost,127.0.0.1'
    assert observed[-1]['NODE_USE_ENV_PROXY'] == '1'
    assert 'HMW_DEV_PASSWORD' not in observed[-1]
    with pytest.raises(ValueError):
        ReadonlyPiAgent(base,tmp_path / 'agent.mjs',network={'proxy_mode':'mystery'})


def test_node_proxy_and_no_proxy_route_local_requests(tmp_path):
    import json
    import shutil
    import subprocess
    import threading
    import pytest
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from workbench.questions.agent import ReadonlyPiAgent
    node=shutil.which('node')
    if not node:
        pytest.skip('Node not installed')
    version=subprocess.run([node,'--version'],capture_output=True,text=True,check=True).stdout.strip().lstrip('v')
    major,minor,*_=map(int,version.split('.'))
    if not (major >= 24 or (major == 22 and minor >= 21)):
        pytest.skip('Node has no built-in environment proxy support')
    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b'target')
        def log_message(self, *args): pass
    class Proxy(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b'proxy')
        def do_CONNECT(self):
            self.send_response(200); self.end_headers(); self.wfile.flush()
            self.connection.recv(8192)
            self.connection.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\nproxy')
        def log_message(self, *args): pass
    servers=[ThreadingHTTPServer(('127.0.0.1',0),handler) for handler in (Target,Proxy)]
    threads=[threading.Thread(target=server.serve_forever) for server in servers]
    for thread in threads: thread.start()
    try:
        target,proxy=servers
        url=f'http://127.0.0.1:{target.server_port}/'
        proxy_url=f'http://127.0.0.1:{proxy.server_port}'
        base={'api_key':'key','api_url':'https://example.com','model':'deepseek-v4-pro'}
        def request(no_proxy):
            agent=ReadonlyPiAgent(base,tmp_path / 'agent.mjs',network={'proxy_mode':'environment','http_proxy':proxy_url,'no_proxy':no_proxy})
            result=subprocess.run([node,'-e',f'fetch({json.dumps(url)}).then(r=>r.text()).then(t=>process.stdout.write(t))'],
                                  env=agent._agent_environment(),capture_output=True,text=True,timeout=10,check=True)
            return result.stdout
        assert request('') == 'proxy'
        assert request('127.0.0.1') == 'target'
    finally:
        for server in servers: server.shutdown()
        for thread in threads: thread.join()
        for server in servers: server.server_close()
