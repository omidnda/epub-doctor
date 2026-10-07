import json
import sys
from pathlib import Path
from zipfile import BadZipFile

import pytest
from mcp import Client, StdioServerParameters
from mcp.server.mcpserver.exceptions import ToolError

from src import mcp_server, service, validator
from src.zip_inspection import UnsafeZipError


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture
async def client():
    async with Client(mcp_server.mcp, raise_exceptions=True) as connected:
        yield connected


INSPECTION = dict(valid=False, repairable=True, safe_repairs=['mimetype_missing'],
                  fatal_count=0, error_count=1, warning_count=2,
                  messages=[{'ID': 'PKG-006', 'message': 'preserve', 'locations': []}],
                  publication={'title': 'Test', 'hasEncryption': False})


def repair_result(reason=None):
    return dict(original_valid=False, detected=['mimetype_missing'],
                repaired=['mimetype_missing'] if reason is None else [],
                final_valid=reason is None, output_file='out.epub' if reason is None else None,
                remaining_messages=[] if reason is None else [{'ID': 'PKG-006'}], reason=reason)


def assert_structured(result, expected):
    assert result.is_error is False
    assert result.structured_content == expected
    assert json.loads(result.content[0].text) == expected


@pytest.mark.anyio
async def test_exact_tools_and_input_schemas(client):
    listed = await client.list_tools()
    tools = {tool.name: tool for tool in listed.tools}
    assert set(tools) == {'inspect_epub', 'repair_epub'}
    for name, parameters in [('inspect_epub', ['path']),
                              ('repair_epub', ['input_path', 'output_path'])]:
        schema = tools[name].input_schema
        assert set(schema['properties']) == set(parameters)
        assert set(schema['required']) == set(parameters)
        assert all(schema['properties'][key]['type'] == 'string' for key in parameters)
        assert tools[name].output_schema['type'] == 'object'
        assert 'local' in tools[name].description.lower()


@pytest.mark.anyio
async def test_inspect_delegates_and_preserves_schema(client, monkeypatch):
    calls = []
    def inspect(path):
        calls.append(path)
        return INSPECTION
    monkeypatch.setattr(service, 'inspect_epub', inspect)
    assert_structured(await client.call_tool('inspect_epub', {'path': 'relative/book.epub'}),
                      INSPECTION)
    assert calls == ['relative/book.epub']


@pytest.mark.anyio
@pytest.mark.parametrize('reason,detail', [(None, None), ('output_exists', 'keep output'),
                                         ('no_supported_repair', None),
                                         ('epubcheck_failure', 'Java unavailable'),
                                         ('input_read_failed', 'missing input'),
                                         ('invalid_zip', 'corrupt archive')])
async def test_repair_preserves_results(client, monkeypatch, reason, detail):
    expected = repair_result(reason)
    if detail is not None:
        expected['detail'] = detail
    calls = []
    def repair(input_path, output_path):
        calls.append((input_path, output_path))
        return expected
    monkeypatch.setattr(service, 'repair_epub_file', repair)
    assert_structured(await client.call_tool('repair_epub',
                                             {'input_path': 'in.epub', 'output_path': 'out.epub'}),
                      expected)
    assert calls == [('in.epub', 'out.epub')]
    assert mcp_server.repair_epub('in.epub', 'out.epub') is expected


@pytest.mark.anyio
async def test_real_missing_input(client, tmp_path):
    path = str(tmp_path / 'absent.epub')
    result = await client.call_tool('inspect_epub', {'path': path})
    assert result.is_error is True
    assert result.structured_content is None
    assert 'input_not_found:' in result.content[0].text
    repaired = await client.call_tool('repair_epub',
                                      {'input_path': path, 'output_path': str(tmp_path / 'out.epub')})
    assert repaired.is_error is False
    assert repaired.structured_content['reason'] == 'input_read_failed'
    assert not list(tmp_path.iterdir())


@pytest.mark.anyio
@pytest.mark.parametrize('failure', ['jar', 'java', 'report'])
async def test_real_epubcheck_failure(client, monkeypatch, make_epub, tmp_path, failure):
    source = make_epub()
    if failure == 'jar':
        monkeypatch.setattr(validator, 'EPUBCHECK_JAR', tmp_path / 'absent.jar')
    else:
        def run(*args, **kwargs):
            if failure == 'java':
                raise FileNotFoundError('java')
            from subprocess import CompletedProcess
            return CompletedProcess(args[0], 0, '', '')
        monkeypatch.setattr(validator.subprocess, 'run', run)
    result = await client.call_tool('inspect_epub', {'path': str(source)})
    assert result.is_error is True
    assert result.structured_content is None
    assert 'epubcheck_failure:' in result.content[0].text
    repaired = await client.call_tool('repair_epub',
                                      {'input_path': str(source), 'output_path': str(tmp_path / 'out.epub')})
    assert repaired.is_error is False
    assert repaired.structured_content['reason'] == 'epubcheck_failure'
    assert repaired.structured_content['final_valid'] is None
    assert set(tmp_path.iterdir()) == {source}


@pytest.mark.anyio
@pytest.mark.parametrize('error,label', [
    (BadZipFile('broken'), 'invalid_zip'),
    (PermissionError('denied'), 'input_read_failed'),
    (UnsafeZipError('invalid_zip_structure', 'unsafe'), 'invalid_zip_structure'),
    (NotImplementedError('unsupported compression'), 'unsupported_zip'),
])
async def test_expected_diagnosis_errors(client, monkeypatch, error, label):
    def inspect(path):
        raise error
    monkeypatch.setattr(service, 'inspect_epub', inspect)
    result = await client.call_tool('inspect_epub', {'path': 'in.epub'})
    assert result.is_error is True
    assert result.structured_content is None
    assert f'{label}:' in result.content[0].text
    with pytest.raises(ToolError) as caught:
        mcp_server.inspect_epub('in.epub')
    assert caught.value.__cause__ is error


@pytest.mark.anyio
async def test_corrupt_epub_is_normal_validation_data(client, tmp_path):
    source = tmp_path / 'corrupt.epub'
    source.write_bytes(b'not ZIP')
    result = await client.call_tool('inspect_epub', {'path': str(source)})
    assert result.is_error is False
    assert result.structured_content['valid'] is False
    assert result.structured_content['repairable'] is False
    assert result.structured_content['messages']
    repaired = await client.call_tool('repair_epub',
                                      {'input_path': str(source), 'output_path': str(tmp_path / 'out.epub')})
    assert repaired.is_error is False
    assert repaired.structured_content['reason'] == 'invalid_zip'
    assert set(tmp_path.iterdir()) == {source}


@pytest.mark.anyio
@pytest.mark.parametrize('path', ['relative/book.epub', 'https://example.org/book.epub',
                                   'file:///tmp/book.epub', 'data:application/epub+zip;base64,AAAA'])
async def test_paths_passed_verbatim_without_transfer(client, monkeypatch, path):
    calls = []
    def inspect(local_path):
        calls.append(local_path)
        return INSPECTION
    monkeypatch.setattr(service, 'inspect_epub', inspect)
    assert_structured(await client.call_tool('inspect_epub', {'path': path}), INSPECTION)
    assert calls == [path]  # A URL-like string is only a filesystem path to the service.


@pytest.mark.anyio
async def test_bad_arguments_do_not_call_service(client, monkeypatch):
    def inspect(path):
        pytest.fail('service should not run for invalid arguments')
    monkeypatch.setattr(service, 'inspect_epub', inspect)
    assert (await client.call_tool('inspect_epub', {})).is_error is True
    assert (await client.call_tool('inspect_epub', {'path': 123})).is_error is True


@pytest.mark.anyio
@pytest.mark.parametrize('name,error', [('inspect_epub', TypeError('bug')),
                                      ('inspect_epub', RuntimeError('bug')),
                                      ('repair_epub', TypeError('bug'))])
async def test_programming_errors_not_translated(client, monkeypatch, caplog, name, error):
    def fail(*args):
        raise error
    monkeypatch.setattr(service, 'inspect_epub' if name == 'inspect_epub' else 'repair_epub_file', fail)
    function = getattr(mcp_server, name)
    args = ['in.epub'] if name == 'inspect_epub' else ['in.epub', 'out.epub']
    with pytest.raises(type(error)) as caught:
        function(*args)
    assert caught.value is error
    arguments = {'path': 'in.epub'} if name == 'inspect_epub' else dict(input_path='in.epub', output_path='out.epub')
    result = await client.call_tool(name, arguments)
    assert result.is_error is True
    assert 'bug' not in result.content[0].text  # SDK sanitizes and logs unexpected errors.
    assert any(record.exc_info for record in caplog.records)


def test_main_explicit_stdio(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server.mcp, 'run', lambda **kwargs: calls.append(kwargs))
    mcp_server.main()
    assert calls == [{'transport': 'stdio'}]


@pytest.mark.anyio
async def test_real_stdio_smoke(tmp_path):
    root = Path(__file__).resolve().parents[1]
    fixture = root / 'tests/fixtures/rsc002/epub3-missing-container.epub'
    before = fixture.read_bytes()
    output = tmp_path / 'repaired.epub'
    parameters = StdioServerParameters(command=sys.executable, args=['-m', 'src.mcp_server'], cwd=str(root))
    async with Client(parameters, read_timeout_seconds=60) as connected:
        assert {tool.name for tool in (await connected.list_tools()).tools} == {'inspect_epub', 'repair_epub'}
        inspected = await connected.call_tool('inspect_epub', {'path': str(fixture)})
        assert inspected.is_error is False
        assert inspected.structured_content['safe_repairs'] == ['container_xml_missing']
        assert inspected.structured_content['repairable'] is True
        assert not output.exists()
        repaired = await connected.call_tool('repair_epub',
                                               {'input_path': str(fixture), 'output_path': str(output)})
        assert repaired.is_error is False
        assert repaired.structured_content['final_valid'] is True
        assert repaired.structured_content['output_file'] == str(output)
        assert repaired.structured_content['repaired'] == ['container_xml_missing']
        checked = await connected.call_tool('inspect_epub', {'path': str(output)})
        assert checked.structured_content['valid'] is True
        refused = await connected.call_tool('repair_epub',
                                             {'input_path': str(fixture), 'output_path': str(output)})
        assert refused.is_error is False
        assert refused.structured_content['reason'] == 'output_exists'
    assert fixture.read_bytes() == before
    assert set(tmp_path.iterdir()) == {output}
