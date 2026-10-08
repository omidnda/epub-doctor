from threading import Event, Thread

import pytest
from starlette.testclient import TestClient

from src import chatgpt_mcp_server as server, operation_limit
from test_chatgpt_download_delivery import clock, store

ORIGIN = 'https://epubdoctor.example.org'


def test_health_lifespan_and_no_java_execution(store, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Readiness must not run EPUBCheck')
    monkeypatch.setattr(server.validator, 'validate_epub', forbidden)
    app = server.create_download_app(store, ORIGIN)
    with TestClient(app) as http:
        response = http.get('/healthz')
        assert response.status_code == 200 and response.text == 'ok'
        assert response.headers['cache-control'] == 'no-store'
        assert http.post('/healthz').status_code == 405
    assert app.state.ready is False
    assert TestClient(app).get('/healthz').status_code == 503


@pytest.mark.parametrize('missing', ['jar', 'java', 'store'])
def test_readiness_missing_dependency(store, monkeypatch, tmp_path, missing):
    with TestClient(server.create_download_app(store, ORIGIN)) as http:
        if missing == 'jar':
            monkeypatch.setattr(server.validator, 'EPUBCHECK_JAR', tmp_path / 'absent.jar')
        elif missing == 'java':
            monkeypatch.setattr(server.shutil, 'which', lambda name: None)
        else:
            store.close()
        response = http.get('/healthz')
        assert response.status_code == 503 and response.text == 'unavailable'
        assert str(tmp_path) not in response.text


def test_readiness_while_processing_busy(store):
    entered, release = Event(), Event()
    def work():
        with operation_limit.operation_slot():
            entered.set()
            assert release.wait(10)
    worker = Thread(target=work)
    worker.start()
    try:
        assert entered.wait(5)
        with TestClient(server.create_download_app(store, ORIGIN)) as http:
            assert http.get('/healthz').status_code == 200
    finally:
        release.set()
        worker.join(5)
        assert not worker.is_alive()
