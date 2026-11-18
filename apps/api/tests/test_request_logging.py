import json
import logging

from fastapi.testclient import TestClient
from workbench.app import create_app


def test_request_log_has_correlation_and_omits_credentials_and_payload(tmp_path, caplog):
    app = create_app(f"sqlite:///{tmp_path / 'log.db'}", dev_users={'owner':'sensitive-password'})
    with caplog.at_level(logging.INFO, logger='workbench.request'):
        response = TestClient(app).post('/api/dev/session?token=hidden-query', json={'username':'owner','password':'sensitive-password'})
    assert response.status_code == 200
    request_id = response.headers['X-Request-ID']
    events = [json.loads(record.message) for record in caplog.records if record.name == 'workbench.request']
    assert events[-1]['request_id'] == request_id
    assert events[-1]['route'] == '/api/dev/session'
    assert events[-1]['status_code'] == 200
    assert 'sensitive-password' not in caplog.text
    assert 'hidden-query' not in caplog.text
    assert response.cookies['hmw_session'] not in caplog.text


def test_unexpected_error_is_correlated_without_logging_exception_message(tmp_path, caplog):
    secret = 'private-credential-value'
    app = create_app(f'sqlite:///{tmp_path / "errors.db"}', dev_users={'owner':'password'})
    @app.get('/test-failure')
    def failure():
        raise RuntimeError(secret)
    with caplog.at_level(logging.ERROR, logger='workbench.request'):
        response = TestClient(app).get('/test-failure')
    assert response.status_code == 500
    event = json.loads(next(record.message for record in caplog.records if record.name == 'workbench.request'))
    assert event['request_id'] == response.headers['X-Request-ID']
    assert event['error_type'] == 'RuntimeError'
    assert any(frame['function'] == 'failure' for frame in event['frames'])
    assert secret not in caplog.text and secret not in response.text


def test_default_uvicorn_start_emits_request_info(tmp_path):
    import os
    import socket
    import subprocess
    import sys
    import time
    import urllib.request

    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    env = dict(os.environ, HMW_DEV_USERNAME='owner', HMW_DEV_PASSWORD='test-password',
               HMW_DATABASE_URL=f'sqlite:///{tmp_path / "live.sqlite"}', HMW_STORAGE_ROOT=str(tmp_path / 'objects'),
               HMW_LOG_DIR=str(tmp_path / 'logs'))
    env.pop('HMW_CONFIG_PATH', None)
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'workbench.dev:app', '--host', '127.0.0.1',
                                '--port', str(port), '--no-access-log'], env=env, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 10
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while True:
            try:
                with opener.open(f'http://127.0.0.1:{port}/openapi.json', timeout=0.5) as response:
                    assert response.status == 200
                break
            except OSError:
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise
                time.sleep(.1)
    finally:
        process.terminate()
        _, stderr = process.communicate(timeout=10)
    events=[]
    for line in stderr.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    assert any(event.get('event') == 'http_request' and event.get('level') == 'INFO' for event in events)
    assert 'test-password' not in stderr


def test_application_logging_persists_and_rotates_without_duplicate_handlers(tmp_path):
    import subprocess
    import sys
    script = '''
import logging, os, sys
from pathlib import Path
from workbench.logging_utils import configure_logging, log_event
configure_logging('api', Path(sys.argv[1]))
configure_logging('api', Path(sys.argv[1]))
logger = logging.getLogger('workbench.request')
log_event(logger, logging.INFO, 'api', 'example', request_id='abc')
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0
    files = list(tmp_path.glob('api-*.jsonl'))
    assert len(files) == 1
    events = [json.loads(line) for line in files[0].read_text().splitlines()]
    assert len(events) == 1 and events[0]['request_id'] == 'abc'
    assert sum('"event": "example"' in line for line in result.stderr.splitlines()) == 1


def test_file_log_rotation_and_private_permissions(tmp_path):
    import os
    import subprocess
    import sys
    script = '''
import logging, sys
from pathlib import Path
from workbench.logging_utils import configure_logging, log_event
configure_logging('worker', Path(sys.argv[1]))
handler = next(h for h in logging.getLogger('workbench').handlers if hasattr(h, 'maxBytes'))
handler.maxBytes = 256
for i in range(5):
    log_event(logging.getLogger('workbench.worker'), logging.INFO, 'worker', 'test', index=i)
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0
    files = list(tmp_path.glob('worker-*.jsonl*'))
    assert len(files) > 1
    assert len(files) <= 4
    if os.name != 'nt':
        assert all(file.stat().st_mode & 0o077 == 0 for file in files)


def test_worker_start_and_shutdown_are_persisted(tmp_path):
    import os
    import pytest
    if os.name == 'nt':
        pytest.skip('Windows terminate is not a catchable SIGTERM')
    import subprocess
    import sys
    import time
    log_dir = tmp_path / 'logs'
    env = dict(os.environ, HMW_DATABASE_URL=f'sqlite:///{tmp_path / "worker.sqlite"}',
               HMW_STORAGE_ROOT=str(tmp_path / 'objects'), HMW_LOG_DIR=str(log_dir))
    env.pop('HMW_ANALYSIS_CONFIG_PATH', None)
    process = subprocess.Popen([sys.executable, '-m', 'workbench.jobs.worker'], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 10
        while True:
            files = list(log_dir.glob('worker-*.jsonl')) if log_dir.exists() else []
            if files and 'worker_started' in files[0].read_text():
                break
            if process.poll() is not None or time.monotonic() >= deadline:
                raise AssertionError('worker did not start')
            time.sleep(.1)
    finally:
        process.terminate()
        _, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, stderr
    events = [json.loads(line) for line in files[0].read_text().splitlines()]
    assert [event['event'] for event in events] == ['worker_started', 'worker_stopped']
    assert events[0]['worker_id'] == events[-1]['worker_id']


def test_existing_log_file_permissions_are_private(tmp_path):
    import os
    import subprocess
    import sys
    import pytest
    if os.name == 'nt':
        pytest.skip('POSIX file permissions')
    script = '''
import os, sys
from pathlib import Path
from workbench.logging_utils import configure_logging
p = Path(sys.argv[1]) / f'api-{os.getpid()}.jsonl'
p.write_text('')
p.chmod(0o644)
configure_logging('api', Path(sys.argv[1]))
assert p.stat().st_mode & 0o077 == 0
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr


def test_log_file_symlink_is_rejected(tmp_path):
    import os
    import subprocess
    import sys
    import pytest
    if os.name == 'nt':
        pytest.skip('POSIX symlink security')
    victim = tmp_path / 'victim'
    victim.write_text('original')
    script = '''
import os, sys
from pathlib import Path
from workbench.logging_utils import configure_logging
p = Path(sys.argv[1]) / f'api-{os.getpid()}.jsonl'
p.symlink_to(Path(sys.argv[1]) / 'victim')
try:
    configure_logging('api', Path(sys.argv[1]))
except OSError:
    sys.exit(0)
sys.exit(1)
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0 and victim.read_text() == 'original'


def test_worker_startup_error_is_structured_and_does_not_expose_database_url(tmp_path):
    import os
    import subprocess
    import sys
    log_dir = tmp_path / 'logs'
    secret = 'private-database-value'
    env = dict(os.environ, HMW_DATABASE_URL=f'unknown://{secret}', HMW_LOG_DIR=str(log_dir))
    result = subprocess.run([sys.executable, '-m', 'workbench.jobs.worker'], env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    events = [json.loads(line) for line in next(log_dir.glob('worker-*.jsonl')).read_text().splitlines()]
    assert any(item['event'] == 'worker_failed' and item['error_type'] for item in events)
    assert secret not in result.stderr and secret not in json.dumps(events)
