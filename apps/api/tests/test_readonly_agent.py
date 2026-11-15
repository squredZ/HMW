from fastapi.testclient import TestClient
from workbench.app import create_app
from test_guest_questions import submit


def test_agent_receives_only_shared_evidence_and_no_write_capability(tmp_path):
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
        assert len(calls) == 1 and len(calls[0][1]) == 1
        assert '私密' not in str(calls)
        assert data['evidence'][0]['excerpt'] == '接口版本是 V2。'


def test_revoked_knowledge_is_not_returned_after_agent_completes(tmp_path):
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
        assert response.status_code == 409
        assert 'V2' not in response.text


def test_agent_failure_after_revocation_does_not_return_old_excerpt(tmp_path):
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
        assert response.status_code == 409 and 'V2' not in response.text


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


def test_no_evidence_never_calls_agent(tmp_path):
    root = tmp_path / 'objects'
    app = create_app(f"sqlite:///{tmp_path / 'db.sqlite'}", dev_users={'owner':'password'}, storage_root=root)
    class ForbiddenAgent:
        def answer(self, question, evidence):
            raise AssertionError('agent must not run without evidence')
    app.state.answer_agent = ForbiddenAgent()
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
    def run(command, payload, env):
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
