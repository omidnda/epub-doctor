import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from mcp import Client
from starlette.testclient import TestClient

from src import chatgpt_mcp_server as server, service, temporary_downloads as downloads, validator
from test_chatgpt_files import REFERENCE, Response, network, workspace_root
from test_chatgpt_mcp_server import INSPECTION

ORIGIN = 'https://epubdoctor.example.org'
PAYLOAD = b'exact validated repaired EPUB bytes'


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture
def clock():
    return [1_700_000_000.0]


@pytest.fixture
def store(tmp_path, clock):
    item = downloads.TemporaryDownloadStore(tmp_path / 'published', clock=lambda: clock[0])
    yield item
    item.close()


@pytest.fixture
def fake_repair(monkeypatch):
    seen = []
    results = []
    def repair(source, output):
        source, output = Path(source), Path(output)
        assert source.read_bytes() == b'EPUB'
        output.write_bytes(PAYLOAD)
        seen.append(source.parent)
        result = dict(original_valid=False, detected=['mimetype_missing'], repaired=['mimetype_missing'],
                      final_valid=True, output_file=str(output), remaining_messages=[], reason=None, detail='retain')
        results.append(result)
        return result
    monkeypatch.setattr(service, 'repair_epub_file', repair)
    return seen, results


def call(http, name='repair_epub', file=None, headers=None):
    response = http.post('/mcp', headers={
        'Accept': 'application/json, text/event-stream', 'Content-Type': 'application/json',
        'MCP-Protocol-Version': '2025-11-25', **(headers or {})}, json={
        'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
        'params': {'name': name, 'arguments': {'file': file or REFERENCE}}})
    assert response.status_code == 200, response.text
    return response.json()['result']


def test_full_http_tool_publication_exact_download_and_cleanup(store, fake_repair, network, workspace_root):
    app = server.create_download_app(store, ORIGIN)
    with TestClient(app, base_url='http://127.0.0.1:8000') as http:
        result = call(http, file={**REFERENCE, 'file_name': 'book.epub'})
        assert not result['isError']
        content = result['structuredContent']
        delivery = content['file_output']
        assert set(delivery) == {'status', 'delivered', 'download_url', 'expires_in_seconds', 'file_name'}
        assert delivery['status'] == 'available' and delivery['delivered'] is True
        assert delivery['expires_in_seconds'] == 900
        assert delivery['file_name'] == 'book-repaired.epub'
        assert delivery['download_url'].startswith(ORIGIN + '/download/')
        assert content['repair'] == {**fake_repair[1][0], 'output_file': None}
        assert fake_repair[1][0]['output_file'] is not None  # Core result unchanged.
        assert not fake_repair[0][0].exists()
        assert set(workspace_root.iterdir()) == {store.root}
        response = http.get(urlsplit(delivery['download_url']).path)
        assert response.status_code == 200 and response.content == PAYLOAD
        assert response.headers['content-type'] == 'application/epub+zip'
        assert response.headers['content-disposition'] == 'attachment; filename="book-repaired.epub"'
        assert response.headers['cache-control'] == 'no-store'
        assert response.headers['referrer-policy'] == 'no-referrer'
        assert response.headers['x-content-type-options'] == 'nosniff'
        assert http.get(urlsplit(delivery['download_url']).path).content == PAYLOAD
        assert str(store.root) not in json.dumps(result)
        assert 'TOP_SECRET' not in json.dumps(result)


@pytest.mark.anyio
async def test_official_sdk_tools_schema_annotations_and_delivery(store, fake_repair, network):
    configured = server.create_server(store=store, origin=ORIGIN)
    async with Client(configured) as client:
        tools = (await client.list_tools()).tools
        assert {tool.name for tool in tools} == {'inspect_epub', 'repair_epub'}
        repair = next(tool for tool in tools if tool.name == 'repair_epub')
        assert repair.annotations.read_only_hint is False
        assert repair.annotations.destructive_hint is False
        assert repair.annotations.open_world_hint is True
        assert set(repair.input_schema['properties']) == {'file'}
        assert set(repair.output_schema['properties']) == {'repair', 'file_output'}
        assert 'external temporary download link' in repair.description
        result = await client.call_tool('repair_epub', {'file': REFERENCE})
        assert not result.is_error
        content = result.structured_content
        assert content['repair']['detail'] == 'retain'
        assert content['file_output']['file_name'] == 'repaired.epub'
        token = content['file_output']['download_url'].rsplit('/', 1)[1]
        opened = store.open(token)
        with opened.stream:
            assert opened.stream.read() == PAYLOAD


@pytest.mark.parametrize('overrides', [
    {'final_valid': False}, {'final_valid': None}, {'final_valid': 1},
    {'reason': 'no_supported_repair', 'repaired': []}, {'reason': 'revalidation_failed'},
    {'reason': 'epubcheck_failure'}, {'reason': 'repair_failed'}, {'original_valid': True, 'repaired': []},
    {'output_file': None}, {'output_file': '/etc/passwd'},
])
def test_no_publication_on_refusal_failure_or_missing_success_evidence(
        store, network, monkeypatch, overrides):
    def repair(source, output):
        Path(output).write_bytes(PAYLOAD)  # Even an existing intermediate is never exposed.
        return dict(original_valid=False, detected=['x'], repaired=['x'], final_valid=True,
                    output_file=str(output), remaining_messages=[], reason=None) | overrides
    monkeypatch.setattr(service, 'repair_epub_file', repair)
    configured = server.create_download_app(store, ORIGIN)
    with TestClient(configured, base_url='http://127.0.0.1:8000') as http:
        result = call(http)
    assert not result['isError']
    assert result['structuredContent']['file_output']['delivered'] is False
    assert 'download_url' not in result['structuredContent']['file_output']
    assert set(entry.name for entry in store.root.iterdir()) == {'.lock'}


@pytest.mark.parametrize('failure', ['missing', 'symlink', 'oversize', 'disk_full'])
def test_publication_failure_does_not_claim_delivery_or_change_repair(
        store, network, monkeypatch, failure, fake_repair):
    original = service.repair_epub_file
    def repair(source, output):
        result = original(source, output)
        if failure == 'missing':
            Path(output).unlink()
        elif failure == 'symlink':
            Path(output).unlink()
            Path(output).symlink_to(source)
        return result
    monkeypatch.setattr(service, 'repair_epub_file', repair)
    if failure == 'oversize':
        monkeypatch.setattr(downloads, 'MAX_OUTPUT_BYTES', 3)
    if failure == 'disk_full':
        monkeypatch.setattr(downloads.os, 'link', lambda *a, **kw: (_ for _ in ()).throw(OSError('SECRET')))
    with TestClient(server.create_download_app(store, ORIGIN), base_url='http://127.0.0.1:8000') as http:
        result = call(http)
    assert not result['isError']
    content = result['structuredContent']
    assert content['repair']['final_valid'] is True
    assert content['repair']['reason'] is None
    assert content['file_output']['status'] == 'failed'
    assert content['file_output']['delivered'] is False
    assert 'download_url' not in content['file_output']
    assert 'SECRET' not in json.dumps(result)
    assert set(entry.name for entry in store.root.iterdir()) == {'.lock'}


def test_http_expiry_unknown_malformed_paths_and_no_listing(store, fake_repair, network, clock):
    with TestClient(server.create_download_app(store, ORIGIN), base_url='http://127.0.0.1:8000') as http:
        output = call(http)['structuredContent']['file_output']
        path = urlsplit(output['download_url']).path
        for bad in ['/download/bad', '/download/' + 'A' * 43, '/download/', '/download/../../etc/passwd',
                    '/download/%2e%2e%2fsecret', '/download/.lock', '/download/' + 'a' * 100]:
            response = http.get(bad)
            assert response.status_code == 404
            assert PAYLOAD not in response.content
        assert http.post(path).status_code == 405
        clock[0] += 900
        assert http.get(path).status_code == 404
        assert set(entry.name for entry in store.root.iterdir()) == {'.lock'}


def test_host_headers_cannot_choose_output_origin(store, fake_repair, network):
    with TestClient(server.create_download_app(store, ORIGIN), base_url='http://127.0.0.1:8000') as http:
        result = call(http, headers={'X-Forwarded-Host': 'evil.example.org', 'X-Forwarded-Proto': 'http'})
        assert result['structuredContent']['file_output']['download_url'].startswith(ORIGIN + '/')
        response = http.post('/mcp', headers={'Host': 'evil.example.org'}, json={})
        assert response.status_code == 421


def test_inspection_is_unchanged_and_never_publishes(store, network, monkeypatch):
    monkeypatch.setattr(service, 'inspect_epub', lambda path: INSPECTION)
    with TestClient(server.create_download_app(store, ORIGIN), base_url='http://127.0.0.1:8000') as http:
        result = call(http, name='inspect_epub')
    assert result['structuredContent'] == INSPECTION
    assert set(entry.name for entry in store.root.iterdir()) == {'.lock'}


def test_real_opf014_download_independently_validates(store, network, workspace_root, tmp_path):
    fixture = Path(__file__).parent / 'fixtures/opf014/package-manifest-prop-scripted-undeclared-javascript-error.epub'
    original = fixture.read_bytes()
    network['response'] = Response(chunks=(original,))
    with TestClient(server.create_download_app(store, ORIGIN), base_url='http://127.0.0.1:8000') as http:
        result = call(http, file={**REFERENCE, 'file_name': fixture.name})
        assert not result['isError']
        content = result['structuredContent']
        assert content['repair']['final_valid'] is True
        assert content['repair']['repaired'] == ['manifest_missing_required_property']
        assert content['file_output']['delivered'] is True
        response = http.get(urlsplit(content['file_output']['download_url']).path)
        assert response.status_code == 200
        assert set(workspace_root.iterdir()) == {store.root}
        downloaded = tmp_path / 'independently-downloaded.epub'
        downloaded.write_bytes(response.content)
        checked = validator.validate_epub(str(downloaded))
        assert checked['valid'] is True
        assert checked['fatal_count'] == checked['error_count'] == 0
        assert fixture.read_bytes() == original


def test_configured_cli_security_storage_and_cleanup(monkeypatch, tmp_path):
    seen = []
    def run(store, origin, host, port, security):
        seen.append(store)
        assert origin == ORIGIN
        assert host == '127.0.0.1' and port == 8123
        assert urlsplit(ORIGIN).netloc in security.allowed_hosts
        assert ORIGIN in security.allowed_origins
        assert store._root_fd is not None
    monkeypatch.setattr(server, '_run_download_http', run)
    server.main(['--transport', 'streamable-http', '--public-origin', ORIGIN,
                 '--download-directory', str(tmp_path / 'outputs'), '--port', '8123'])
    assert len(seen) == 1 and seen[0]._root_fd is None


@pytest.mark.parametrize('args', [['--public-origin', ORIGIN],
    ['--transport', 'streamable-http', '--public-origin', 'http://example.org']])
def test_invalid_delivery_cli_configuration(args):
    with pytest.raises(SystemExit) as exc:
        server.main(args)
    assert exc.value.code == 2


def test_runner_disables_access_logs(store, monkeypatch):
    import uvicorn
    configs = []
    class FakeServer:
        def __init__(self, config):
            configs.append(config)
        def run(self):
            pass
    monkeypatch.setattr(uvicorn, 'Server', FakeServer)
    server._run_download_http(store, ORIGIN, '127.0.0.1', 8000, None)
    assert configs[0].access_log is False
    assert configs[0].log_level == 'info'


def test_periodic_cleanup_runs_without_a_download_request(store, clock, monkeypatch, tmp_path):
    import time
    source = tmp_path / 'validated.epub'
    source.write_bytes(PAYLOAD)
    token = store.publish(source).token
    monkeypatch.setattr(server, 'CLEANUP_INTERVAL_SECONDS', 0.01)
    with TestClient(server.create_download_app(store, ORIGIN), base_url='http://127.0.0.1:8000'):
        clock[0] += 900
        deadline = time.monotonic() + 2
        while (store.root / (token + '.epub')).exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not (store.root / (token + '.epub')).exists()


def test_publication_logs_contain_no_capability_or_signed_input_url(store, fake_repair, network, caplog):
    with TestClient(server.create_download_app(store, ORIGIN), base_url='http://127.0.0.1:8000') as http:
        result = call(http)
        token = result['structuredContent']['file_output']['download_url'].rsplit('/', 1)[1]
        # httpx/TestClient logs URLs itself; this test checks the server's tool and
        # publication log path, while the runner test covers real access logging.
    assert token not in caplog.text
    assert 'TOP_SECRET' not in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize('failure', ['stalled', 'disconnected', 'programming_error'])
async def test_response_closes_descriptor_even_before_streaming(store, tmp_path, monkeypatch, failure):
    import anyio
    source = tmp_path / 'validated.epub'
    source.write_bytes(PAYLOAD)
    opened = store.open(store.publish(source).token)
    async def chunks():
        yield opened.stream.read()
    async def receive():
        await anyio.sleep_forever()
    async def send(message):
        if failure == 'stalled':
            await anyio.sleep_forever()
        elif failure == 'disconnected':
            raise OSError('client disconnected')
        else:
            raise TypeError('programming bug')
    monkeypatch.setattr(server, 'MAX_DOWNLOAD_SECONDS', 0.01)
    response = server._DownloadResponse(chunks(), stream=opened.stream)
    scope = {'type': 'http', 'method': 'GET', 'asgi': {'spec_version': '2.4'}}
    if failure == 'programming_error':
        with pytest.raises(TypeError, match='programming bug'):
            await response(scope, receive, send)
    else:
        await response(scope, receive, send)
    assert opened.stream.closed
