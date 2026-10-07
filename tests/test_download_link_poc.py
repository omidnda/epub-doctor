import json

import pytest
from mcp import Client
from starlette.testclient import TestClient

from src import download_link_poc as poc
from src import chatgpt_mcp_server, mcp_server


ORIGIN = 'https://download-poc.example.org'


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.mark.anyio
async def test_probe_is_isolated_and_returns_model_visible_link():
    async with Client(poc.create_server(ORIGIN)) as client:
        tools = (await client.list_tools()).tools
        assert [tool.name for tool in tools] == ['get_download_link_poc']
        assert tools[0].input_schema['properties'] == {}
        assert tools[0].annotations.read_only_hint is True
        assert tools[0].annotations.destructive_hint is False
        assert tools[0].annotations.open_world_hint is False
        assert not tools[0].meta
        result = await client.call_tool('get_download_link_poc', {})
        assert not result.is_error
        expected = {'url': ORIGIN + poc.PROBE_PATH, 'file_name': poc.PROBE_FILENAME,
                    'detail': 'Development probe only: fixed public text, no EPUB or user data.'}
        assert result.structured_content == expected
        assert json.loads(result.content[0].text) == expected
    for existing in (chatgpt_mcp_server.mcp, mcp_server.mcp):
        async with Client(existing) as client:
            assert {tool.name for tool in (await client.list_tools()).tools} == {'inspect_epub', 'repair_epub'}


def test_http_probe_initializes_and_serves_only_fixed_public_text(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    server = poc.create_server(ORIGIN)
    app = server.streamable_http_app(stateless_http=True, json_response=True, host='127.0.0.1')
    with TestClient(app, base_url='http://127.0.0.1:8001') as http:
        headers = {'Accept': 'application/json, text/event-stream'}
        response = http.post('/mcp', headers=headers, json={
            'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                'protocolVersion': '2025-11-25', 'capabilities': {},
                'clientInfo': {'name': 'link-poc-smoke', 'version': '1.0'}}})
        assert response.status_code == 200
        headers['MCP-Protocol-Version'] = response.json()['result']['protocolVersion']
        result = http.post('/mcp', headers=headers, json={
            'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
            'params': {'name': 'get_download_link_poc', 'arguments': {}}})
        assert result.status_code == 200
        assert result.json()['result']['structuredContent']['url'] == ORIGIN + poc.PROBE_PATH
        downloaded = http.get(poc.PROBE_PATH)
        assert downloaded.content == poc.PROBE_BYTES
        assert downloaded.headers['Content-Type'] == 'text/plain; charset=utf-8'
        assert downloaded.headers['Content-Disposition'] == f'attachment; filename="{poc.PROBE_FILENAME}"'
        assert downloaded.headers['Cache-Control'] == 'no-store'
        assert downloaded.headers['Referrer-Policy'] == 'no-referrer'
        assert downloaded.headers['X-Content-Type-Options'] == 'nosniff'
        assert http.get('/download/anything').status_code == 404
        assert http.get('/input.epub').status_code == 404
        assert http.post(poc.PROBE_PATH).status_code == 405
        assert http.post('/mcp', headers={**headers, 'Host': 'attacker.example'}, json={}).status_code == 421
        assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('origin', [
    'http://example.org', 'https://localhost', 'https://127.0.0.1',
    'https://user:secret@example.org', 'https://example.org/path',
    'https://example.org/?secret=x', 'https://example.org/#',
    'https://example.org:8001', 'https://example.org:bad',
    'https://example.org\n', 'https://example.org/é',
    'https://example.org\\@attacker.example',
])
def test_invalid_operator_origin_rejected(origin):
    with pytest.raises(ValueError, match='public origin'):
        poc.create_server(origin)


def test_http_entrypoint_retains_host_checks(monkeypatch):
    server = poc.create_server(ORIGIN)
    calls = []
    monkeypatch.setattr(poc, 'create_server', lambda origin: server)
    monkeypatch.setattr(server, 'run', lambda **kwargs: calls.append(kwargs))
    poc.main(['--public-origin', ORIGIN + '/'])
    call = calls.pop()
    assert call['transport'] == 'streamable-http'
    assert (call['host'], call['port'], call['streamable_http_path']) == ('127.0.0.1', 8001, '/mcp')
    assert call['transport_security'].enable_dns_rebinding_protection is True
    assert 'download-poc.example.org' in call['transport_security'].allowed_hosts
    assert ORIGIN in call['transport_security'].allowed_origins
