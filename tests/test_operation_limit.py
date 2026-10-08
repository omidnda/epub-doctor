import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, BoundedSemaphore, Event, Lock, Thread, local

import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from src import chatgpt_mcp_server, mcp_server, operation_limit, service
from src.validator import EPUBCheckError


@pytest.fixture(autouse=True)
def isolated_limit(monkeypatch):
    monkeypatch.setattr(operation_limit, '_slots', BoundedSemaphore(1))
    monkeypatch.setattr(operation_limit, '_owned', local())


@pytest.fixture
def occupied():
    entered, release = Event(), Event()
    def hold():
        with operation_limit.operation_slot():
            entered.set()
            assert release.wait(10)
    worker = Thread(target=hold)
    worker.start()
    assert entered.wait(5)
    try:
        yield
    finally:
        release.set()
        worker.join(5)
        assert not worker.is_alive()


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.mark.parametrize('capacity', [1, 2])
def test_fifty_mixed_requests_share_capacity(monkeypatch, capacity):
    monkeypatch.setattr(operation_limit, '_slots', BoundedSemaphore(capacity))
    start = Barrier(51)
    release, rejected_all, admitted_all = Event(), Event(), Event()
    counts = {'active': 0, 'maximum': 0, 'rejected': 0, 'started': 0}
    lock = Lock()
    validation = dict(valid=True, fatal_count=0, error_count=0, warning_count=0,
                      messages=[], publication={})
    def heavy(*args, **kwargs):
        with lock:
            counts['active'] += 1
            counts['started'] += 1
            counts['maximum'] = max(counts['maximum'], counts['active'])
            if counts['started'] == capacity:
                admitted_all.set()
        try:
            assert release.wait(10)
            return validation
        finally:
            with lock:
                counts['active'] -= 1
    monkeypatch.setattr(service, 'validate_epub', heavy)
    monkeypatch.setattr(service, 'diagnose_epub', lambda *args: [])
    monkeypatch.setattr(service, 'repair_epub', heavy)
    def request(index):
        start.wait(10)
        try:
            if index % 2:
                service.inspect_epub('input.epub')
            else:
                service.repair_epub_file('input.epub', 'output.epub')
            return 'accepted'
        except operation_limit.ServerBusyError:
            with lock:
                counts['rejected'] += 1
                if counts['rejected'] == 50 - capacity:
                    rejected_all.set()
            return 'busy'
    with ThreadPoolExecutor(max_workers=50) as pool:
        futures = [pool.submit(request, index) for index in range(50)]
        start.wait(10)
        try:
            assert rejected_all.wait(5), 'Excess requests must not queue'
            assert admitted_all.wait(5)
            assert counts['maximum'] == counts['started'] == capacity
        finally:
            release.set()
        outcomes = [future.result(5) for future in futures]
    assert outcomes.count('accepted') == capacity
    assert outcomes.count('busy') == 50 - capacity
    with operation_limit.operation_slot():
        pass


@pytest.mark.parametrize('entrypoint', ['inspect', 'repair'])
@pytest.mark.parametrize('error', [EPUBCheckError('timeout'), TypeError('bug')])
def test_slot_released_and_original_exception_preserved(monkeypatch, entrypoint, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(service, 'validate_epub' if entrypoint == 'inspect' else 'repair_epub', fail)
    with pytest.raises(type(error)) as caught:
        if entrypoint == 'inspect':
            service.inspect_epub('input.epub')
        else:
            service.repair_epub_file('input.epub', 'output.epub')
    assert caught.value is error
    def another_thread():
        with operation_limit.operation_slot():
            return True
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(another_thread).result(5) is True


def test_nested_calls_reuse_slot(monkeypatch):
    validation = dict(valid=True, fatal_count=0, error_count=0, warning_count=0,
                      messages=[], publication={})
    monkeypatch.setattr(service, 'validate_epub', lambda *args: validation)
    monkeypatch.setattr(service, 'diagnose_epub', lambda *args: [])
    with operation_limit.operation_slot():
        assert service.inspect_epub('input.epub')['valid'] is True
        with operation_limit.operation_slot():
            assert service.inspect_epub('input.epub')['valid'] is True
    with operation_limit.operation_slot():
        pass


@pytest.mark.parametrize('entrypoint', ['inspect', 'repair'])
def test_busy_service_starts_no_work(occupied, monkeypatch, entrypoint, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail('Busy requests must not start processing or telemetry')
    monkeypatch.setattr(service, 'validate_epub', forbidden)
    monkeypatch.setattr(service, 'repair_epub', forbidden)
    monkeypatch.setattr(service.telemetry, 'start', forbidden)
    with pytest.raises(operation_limit.ServerBusyError, match='server_busy'):
        if entrypoint == 'inspect':
            service.inspect_epub(str(tmp_path / 'input.epub'))
        else:
            service.repair_epub_file(str(tmp_path / 'input.epub'), str(tmp_path / 'output.epub'))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('entrypoint', ['inspect', 'repair'])
def test_local_mcp_busy_error(occupied, entrypoint):
    with pytest.raises(ToolError, match='server_busy'):
        if entrypoint == 'inspect':
            mcp_server.inspect_epub('input.epub')
        else:
            mcp_server.repair_epub('input.epub', 'output.epub')


@pytest.mark.anyio
@pytest.mark.parametrize('name', ['inspect_epub', 'repair_epub'])
async def test_chatgpt_sdk_rejects_before_download(occupied, monkeypatch, name):
    def forbidden(*args, **kwargs):
        pytest.fail('Busy requests must not download or create a workspace')
    monkeypatch.setattr(chatgpt_mcp_server, 'downloaded_epub', forbidden)
    async with Client(chatgpt_mcp_server.create_server()) as client:
        result = await client.call_tool(name, {'file': {
            'file_id': 'private-id', 'download_url': 'https://example.org/private-url'}})
    assert result.is_error
    assert result.structured_content is None
    assert 'server_busy' in result.content[0].text
    assert 'private-' not in result.content[0].text


@pytest.mark.parametrize('value,expected', [(None, 1), ('2', 2), ('0', None), ('-1', None), ('bad', None)])
def test_startup_configuration(value, expected):
    environment = os.environ.copy()
    environment.pop('EPUB_DOCTOR_MAX_CONCURRENT_OPERATIONS', None)
    if value is not None:
        environment['EPUB_DOCTOR_MAX_CONCURRENT_OPERATIONS'] = value
    result = subprocess.run([sys.executable, '-c',
                             'from src.operation_limit import MAX_CONCURRENT_OPERATIONS; print(MAX_CONCURRENT_OPERATIONS)'],
                            env=environment, capture_output=True, text=True, timeout=10)
    if expected is None:
        assert result.returncode != 0
    else:
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(expected)
