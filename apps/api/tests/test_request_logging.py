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
