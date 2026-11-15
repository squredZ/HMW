import importlib.util
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[3]


def load_launcher():
    spec = importlib.util.spec_from_file_location('hmw_start', ROOT / 'scripts' / 'start.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_start_configuration_validates_credentials_and_ports(tmp_path):
    launcher = load_launcher()
    config = tmp_path / 'config.toml'
    config.write_text('[owner]\nusername="owner"\npassword=""\n[server]\napi_port=8000\nweb_port=5173\n')
    with pytest.raises(ValueError, match='password'):
        launcher.read_config(config)
    config.write_text('[owner]\nusername="owner"\npassword="secret"\n[server]\napi_port=8000\nweb_port=8000\n')
    with pytest.raises(ValueError, match='port'):
        launcher.read_config(config)
    config.write_text('[owner]\nusername="owner"\npassword="secret"\n[server]\napi_port=8010\nweb_port=5173\n[llm]\napi_key="unused-secret"\n')
    settings = launcher.read_config(config)
    assert settings['owner']['username'] == 'owner'
    assert settings['server']['api_port'] == 8010


@pytest.mark.skipif(os.name == 'nt', reason='POSIX shell wrapper')
def test_start_missing_config_has_clear_instruction(tmp_path):
    result = subprocess.run([str(ROOT / 'scripts' / 'start.sh'), str(tmp_path / 'missing.toml')],
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert 'config.example.toml' in result.stderr


def test_launcher_stops_children_when_a_service_exits(monkeypatch, tmp_path):
    launcher = load_launcher()
    spawned = []
    stopped = []
    class Child:
        pid = 101
        returncode = 1
        calls = 0
        def poll(self):
            self.calls += 1
            return None if self.calls == 1 else 1
    def spawn(command, **kwargs):
        spawned.append((command, kwargs))
        return Child()
    vite = tmp_path / 'node_modules' / 'vite' / 'bin' / 'vite.js'
    vite.parent.mkdir(parents=True)
    vite.touch()
    monkeypatch.setattr(launcher, 'WEB_ROOT', tmp_path)
    monkeypatch.setattr(launcher.shutil, 'which', lambda name: '/test/node')
    monkeypatch.setattr(launcher.subprocess, 'Popen', spawn)
    monkeypatch.setattr(launcher, 'check_ports', lambda ports: None)
    monkeypatch.setattr(launcher, 'wait_ready', lambda *args: None)
    monkeypatch.setattr(launcher, 'stop_children', lambda children: stopped.extend(children))
    config = {'owner':{'username':'owner','password':'test-password'}, 'server':{'api_port':8010,'web_port':5173}}
    config_path = tmp_path / 'config.toml'
    assert launcher.run(config, config_path) == 1
    assert len(stopped) == 3
    assert spawned[0][1]['env']['HMW_DEV_PASSWORD'] == 'test-password'
    assert 'HMW_DEV_PASSWORD' not in spawned[1][1]['env']
    assert 'HMW_DEV_PASSWORD' not in spawned[2][1]['env']
    assert spawned[2][1]['env']['HMW_API_PORT'] == '8010'
    assert spawned[0][1]['env']['HMW_CONFIG_PATH'] == str(config_path.resolve())
    assert 'HMW_CONFIG_PATH' not in spawned[1][1]['env']
    assert 'HMW_CONFIG_PATH' not in spawned[2][1]['env']


def test_launcher_refuses_occupied_ports():
    import socket
    launcher = load_launcher()
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        with pytest.raises(ValueError, match='端口'):
            launcher.check_ports([listener.getsockname()[1]])


def test_readiness_ignores_http_proxy(monkeypatch):
    launcher = load_launcher()
    observed = []
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
    class Opener:
        def open(self, url, timeout):
            observed.append((url, timeout))
            return Response()
    class Child:
        def poll(self): return None
    def opener(handler):
        assert handler.proxies == {}
        return Opener()
    monkeypatch.setattr(launcher, 'build_opener', opener)
    launcher.wait_ready('API', 'http://127.0.0.1:18000/', Child())
    assert observed[0][0] == 'http://127.0.0.1:18000/'
