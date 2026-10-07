import json
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters
from starlette.testclient import TestClient

from src import chatgpt_mcp_server as server, service, validator
from test_chatgpt_files import REFERENCE, Response, network, workspace_root


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture
async def client():
    async with Client(server.mcp, raise_exceptions=True) as connected:
        yield connected


INSPECTION = dict(valid=False, repairable=True, safe_repairs=['mimetype_missing'],
                  fatal_count=0, error_count=1, warning_count=0,
                  messages=[{'ID': 'PKG-006'}], publication={'title': 'Book'})


@pytest.mark.anyio
async def test_official_file_contract_and_exact_tools(client):
    tools = (await client.list_tools()).tools
    assert {tool.name for tool in tools} == {'inspect_epub', 'repair_epub'}
    for tool in tools:
        schema = tool.input_schema
        assert set(schema['properties']) == {'file'}
        assert schema['required'] == ['file']
        file_schema = schema['$defs']['OpenAIFile']
        assert set(file_schema['properties']) == {'download_url', 'file_id', 'mime_type', 'file_name'}
        assert set(file_schema['required']) == {'download_url', 'file_id'}
        assert all(prop['type'] == 'string' for prop in file_schema['properties'].values())
        assert tool.meta == {'openai/fileParams': ['file']}
        assert tool.model_dump(by_alias=True)['_meta']['openai/fileParams'] == ['file']
        assert tool.output_schema['type'] == 'object'
    inspected = next(tool for tool in tools if tool.name == 'inspect_epub')
    assert set(inspected.output_schema['properties']) == set(INSPECTION)


@pytest.mark.anyio
async def test_inspect_download_service_and_cleanup(client, network, workspace_root, monkeypatch):
    seen = []
    def inspect(path):
        path = Path(path)
        seen.append(path.parent)
        assert path.name == 'input.epub'
        assert path.read_bytes() == b'EPUB'
        assert set(item.name for item in path.parent.iterdir()) == {'input.epub'}
        return INSPECTION
    monkeypatch.setattr(service, 'inspect_epub', inspect)
    result = await client.call_tool('inspect_epub', {'file': REFERENCE})
    assert not result.is_error
    assert result.structured_content == INSPECTION
    assert json.loads(result.content[0].text) == INSPECTION
    assert len(seen) == 1 and not seen[0].exists()
    assert not list(workspace_root.iterdir())


@pytest.mark.anyio
@pytest.mark.parametrize('hints', [{}, {'file_name': '../../escape.epub'}, {'mime_type': 'anything'}])
async def test_optional_fields_through_sdk(client, network, workspace_root, monkeypatch, hints):
    monkeypatch.setattr(service, 'inspect_epub', lambda path: INSPECTION)
    result = await client.call_tool('inspect_epub', {'file': {**REFERENCE, **hints}})
    assert not result.is_error
    assert result.structured_content == INSPECTION
    assert not list(workspace_root.iterdir())


@pytest.mark.anyio
@pytest.mark.parametrize('field', ['download_url', 'file_id'])
async def test_missing_required_field_clean(client, network, workspace_root, caplog, field):
    reference = {k: v for k, v in REFERENCE.items() if k != field}
    result = await client.call_tool('inspect_epub', {'file': reference})
    assert result.is_error
    assert result.structured_content is None
    assert field in result.content[0].text
    assert not network['connections']
    assert not list(workspace_root.iterdir())
    assert 'TOP_SECRET' not in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize('status', [302, 403])
async def test_download_failure_tool_error_and_no_secret_logs(client, network, workspace_root, caplog, status):
    network['response'] = Response(status=status)
    result = await client.call_tool('inspect_epub', {'file': REFERENCE})
    assert result.is_error
    assert result.structured_content is None
    assert 'download_' in result.content[0].text
    assert 'TOP_SECRET' not in caplog.text + result.content[0].text
    assert not list(workspace_root.iterdir())


@pytest.mark.anyio
async def test_epubcheck_error_cleanup(client, network, workspace_root, monkeypatch):
    def inspect(path):
        raise validator.EPUBCheckError('Java unavailable')
    monkeypatch.setattr(service, 'inspect_epub', inspect)
    result = await client.call_tool('inspect_epub', {'file': REFERENCE})
    assert result.is_error
    assert 'epubcheck_failure' in result.content[0].text
    assert not list(workspace_root.iterdir())


@pytest.mark.anyio
@pytest.mark.parametrize('reason', ['no_supported_repair', 'revalidation_failed', 'signatures_or_encryption',
                                   'epubcheck_failure', 'already_valid'])
async def test_refused_or_failed_repair_preserved(client, network, workspace_root, monkeypatch, reason):
    expected = dict(original_valid=False, detected=[], repaired=[], final_valid=False,
                    output_file=None, remaining_messages=[{'ID': 'OTHER'}], reason=reason, detail='retain')
    seen = []
    def repair(source, output):
        source, output = Path(source), Path(output)
        seen.append(source.parent)
        assert source.parent == output.parent
        assert output.name == 'repaired.epub'
        assert not output.exists()
        return expected
    monkeypatch.setattr(service, 'repair_epub_file', repair)
    result = await client.call_tool('repair_epub', {'file': REFERENCE})
    assert not result.is_error
    assert result.structured_content['repair'] == expected
    assert result.structured_content['file_output']['delivered'] is False
    assert not seen[0].exists()
    assert not list(workspace_root.iterdir())


@pytest.mark.anyio
async def test_successful_temporary_output_not_fake_delivery(client, network, workspace_root, monkeypatch):
    seen = []
    original_result = {}
    def repair(source, output):
        output = Path(output)
        output.write_bytes(b'validated by service')
        seen.append(output)
        original_result.update(original_valid=False, detected=['mimetype_missing'], repaired=['mimetype_missing'],
                               final_valid=True, output_file=str(output), remaining_messages=[], reason=None)
        return original_result
    monkeypatch.setattr(service, 'repair_epub_file', repair)
    result = await client.call_tool('repair_epub', {'file': REFERENCE})
    assert not result.is_error
    content = result.structured_content
    assert content['repair'] == {**original_result, 'output_file': None}
    assert original_result['output_file'] == str(seen[0])  # Core result untouched.
    assert content['file_output']['status'] == 'unavailable'
    assert content['file_output']['delivered'] is False
    assert not seen[0].exists()
    assert str(seen[0]) not in result.content[0].text
    assert all(block.type == 'text' for block in result.content)
    assert not list(workspace_root.iterdir())


@pytest.mark.anyio
@pytest.mark.parametrize('name', ['inspect_epub', 'repair_epub'])
async def test_programming_errors_not_hidden_and_cleanup(client, network, workspace_root, monkeypatch, name):
    def fail(*args):
        raise TypeError('bug')
    monkeypatch.setattr(service, 'inspect_epub' if name == 'inspect_epub' else 'repair_epub_file', fail)
    with pytest.raises(TypeError, match='bug'):
        getattr(server, name)(REFERENCE)
    assert not list(workspace_root.iterdir())
    result = await client.call_tool(name, {'file': REFERENCE})
    assert result.is_error
    assert not list(workspace_root.iterdir())


@pytest.mark.anyio
@pytest.mark.parametrize('mode,valid,repairable', [('valid', True, False), ('missing', False, True),
                                               ('corrupt', False, False)])
async def test_real_service_inspection_fake_download(client, network, workspace_root, make_epub,
                                                    mode, valid, repairable):
    # Keep the source fixture outside the adapter's temp-root assertions.
    source = make_epub('valid' if mode == 'corrupt' else mode)
    data = b'not a zip' if mode == 'corrupt' else source.read_bytes()
    network['response'] = Response(chunks=(data,), headers={'Content-Length': str(len(data))})
    result = await client.call_tool('inspect_epub', {'file': REFERENCE})
    assert not result.is_error
    assert result.structured_content['valid'] is valid
    assert result.structured_content['repairable'] is repairable
    assert set(workspace_root.iterdir()) == {source}


@pytest.mark.anyio
async def test_real_repairable_fixture_chain(client, network, workspace_root, monkeypatch):
    fixture = Path(__file__).parent / 'fixtures/opf014/package-manifest-prop-scripted-undeclared-javascript-error.epub'
    data = fixture.read_bytes()
    network['response'] = Response(chunks=(data,))
    real_repair = service.repair_epub_file
    generated = []
    def capture(source, output):
        from zipfile import ZipFile
        result = real_repair(source, output)
        assert Path(output).is_file()
        with ZipFile(output) as archive:
            assert b'properties="scripted"' in archive.read('EPUB/package.opf')
        generated.append(Path(output))
        return result
    monkeypatch.setattr(service, 'repair_epub_file', capture)
    result = await client.call_tool('repair_epub', {'file': REFERENCE})
    assert not result.is_error
    assert result.structured_content['repair']['detected'] == ['manifest_missing_required_property']
    assert result.structured_content['repair']['repaired'] == ['manifest_missing_required_property']
    assert result.structured_content['repair']['final_valid'] is True
    assert result.structured_content['repair']['output_file'] is None
    assert result.structured_content['file_output']['delivered'] is False
    assert not list(workspace_root.iterdir())
    assert generated and not generated[0].exists()
    assert fixture.read_bytes() == data


def test_entrypoints_stdio_and_http(monkeypatch):
    calls = []
    monkeypatch.setattr(server.mcp, 'run', lambda **kwargs: calls.append(kwargs))
    server.main([])
    assert calls.pop() == {'transport': 'stdio'}
    server.main(['--transport', 'streamable-http', '--host', '0.0.0.0', '--port', '8123',
                 '--allowed-host', 'dev.example.org', '--allowed-origin', 'https://dev.example.org'])
    call = calls.pop()
    assert call['transport'] == 'streamable-http'
    assert (call['host'], call['port'], call['streamable_http_path']) == ('0.0.0.0', 8123, '/mcp')
    assert call['transport_security'].enable_dns_rebinding_protection is True
    assert 'dev.example.org' in call['transport_security'].allowed_hosts
    assert 'https://dev.example.org' in call['transport_security'].allowed_origins


def test_http_initialization_discovery_and_file_tool(network, workspace_root, monkeypatch):
    monkeypatch.setattr(service, 'inspect_epub', lambda path: INSPECTION)
    app = server.mcp.streamable_http_app(stateless_http=True, json_response=True, host='127.0.0.1')
    headers = {'Accept': 'application/json, text/event-stream', 'Content-Type': 'application/json'}
    with TestClient(app, base_url='http://127.0.0.1:8000') as http:
        initialized = http.post('/mcp', headers=headers, json={
            'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                'protocolVersion': '2025-11-25', 'capabilities': {},
                'clientInfo': {'name': 'epub-smoke', 'version': '1.0'}}})
        assert initialized.status_code == 200, initialized.text
        version = initialized.json()['result']['protocolVersion']
        headers['MCP-Protocol-Version'] = version
        response = http.post('/mcp', headers=headers, json={
            'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}})
        assert response.status_code == 200, response.text
        assert {tool['name'] for tool in response.json()['result']['tools']} == {'inspect_epub', 'repair_epub'}
        inspected = http.post('/mcp', headers=headers, json={
            'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
            'params': {'name': 'inspect_epub', 'arguments': {'file': REFERENCE}}})
        assert inspected.status_code == 200, inspected.text
        assert inspected.json()['result']['structuredContent'] == INSPECTION
        assert not list(workspace_root.iterdir())
        assert http.post('/mcp', headers={**headers, 'Host': 'attacker.example'}, json={}).status_code == 421


@pytest.mark.anyio
async def test_chatgpt_stdio_discovery():
    root = Path(__file__).resolve().parents[1]
    params = StdioServerParameters(command=sys.executable, args=['-m', 'src.chatgpt_mcp_server'], cwd=str(root))
    async with Client(params, read_timeout_seconds=30) as connected:
        tools = (await connected.list_tools()).tools
        assert {tool.name for tool in tools} == {'inspect_epub', 'repair_epub'}
        assert all(tool.meta['openai/fileParams'] == ['file'] for tool in tools)
