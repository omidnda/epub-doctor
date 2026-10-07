import http.client
import socket
from pathlib import Path

import pytest

from src import chatgpt_files as files


REFERENCE = {'download_url': 'https://uploads.example.org/book?token=TOP_SECRET', 'file_id': 'file-123'}


class Response:
    def __init__(self, chunks=(b'EPUB',), headers=None, status=200, error=None):
        self.chunks = iter(chunks)
        self.headers = headers or {}
        self.status = status
        self.error = error
        self.closed = False
        self.reads = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read1(self, size):
        self.reads.append(size)
        if self.error:
            raise self.error
        chunk = next(self.chunks, b'')
        assert len(chunk) <= size  # Match the real bounded HTTPResponse API.
        return chunk


@pytest.fixture
def network(monkeypatch):
    state = {'response': Response(), 'connections': [], 'request_error': None}
    monkeypatch.setattr(files.socket, 'getaddrinfo', lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))])
    class Connection:
        def __init__(self, host, address):
            self.host, self.address, self.closed = host, address, False
            state['connections'].append(self)
        def request(self, *args, **kwargs):
            self.request_args = (args, kwargs)
            if state['request_error']:
                raise state['request_error']
        def getresponse(self):
            return state['response']
        def close(self):
            self.closed = True
    monkeypatch.setattr(files, '_PinnedHTTPSConnection', Connection)
    return state


@pytest.fixture
def workspace_root(monkeypatch, tmp_path):
    original = files.tempfile.TemporaryDirectory
    monkeypatch.setattr(files.tempfile, 'TemporaryDirectory', lambda **kwargs: original(dir=tmp_path, **kwargs))
    return tmp_path


@pytest.mark.parametrize('hints', [{}, {'file_name': '../../x.epub'}, {'mime_type': 'application/epub+zip'},
                                  {'file_name': '/etc/passwd', 'mime_type': 'text/plain'}])
def test_reference_optional_hints_never_paths(network, workspace_root, hints):
    with files.downloaded_epub({**REFERENCE, **hints}) as workspace:
        assert workspace.parent == workspace_root
        assert (workspace / 'input.epub').read_bytes() == b'EPUB'
        assert list(workspace.iterdir()) == [workspace / 'input.epub']
    assert not workspace.exists()
    assert not list(workspace_root.iterdir())
    connection = network['connections'][0]
    assert connection.address == '8.8.8.8'
    assert connection.request_args == (('GET', '/book?token=TOP_SECRET'), {'headers': {'Accept-Encoding': 'identity'}})
    assert connection.closed is True
    assert network['response'].closed is True


@pytest.mark.parametrize('reference', [None, 'path.epub', {},
    {'file_id': 'file-123'}, {'download_url': REFERENCE['download_url']},
    {**REFERENCE, 'file_id': ''}, {**REFERENCE, 'download_url': ''},
    {**REFERENCE, 'file_name': None}, {**REFERENCE, 'mime_type': 3},
])
def test_invalid_reference(reference, workspace_root):
    with pytest.raises(files.FileDownloadError, match='invalid_file_reference') as caught:
        with files.downloaded_epub(reference):
            pytest.fail('must not yield')
    assert 'TOP_SECRET' not in str(caught.value)
    assert not list(workspace_root.iterdir())


@pytest.mark.parametrize('url', ['not a URL', 'http://example.org/a', 'file:///etc/passwd',
    'ftp://example.org/a', 'https://', 'https://user:password@example.org/a',
    'https://example.org/a#fragment', 'https://example.org:444/a',
    'https://example.org:bad/a', 'https://example.org/\nsecret', 'https://example.org/é',
    'https://example.org\\@127.0.0.1/a', 'https://' + 'x' * 17000])
def test_invalid_url(url, network, workspace_root):
    with pytest.raises(files.FileDownloadError, match='invalid_download_url'):
        with files.downloaded_epub({**REFERENCE, 'download_url': url}):
            pytest.fail('must not yield')
    assert network['connections'] == []
    assert not list(workspace_root.iterdir())


@pytest.mark.parametrize('ip', ['127.0.0.1', '10.0.0.1', '172.16.0.1', '192.168.1.1',
    '169.254.169.254', '0.0.0.0', '100.64.0.1', '::1', 'fc00::1', 'fe80::1',
    '::ffff:127.0.0.1', '224.0.0.1'])
def test_non_public_destinations(monkeypatch, network, workspace_root, ip):
    monkeypatch.setattr(files.socket, 'getaddrinfo', lambda *args, **kwargs: [
        (socket.AF_INET6 if ':' in ip else socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 443))])
    with pytest.raises(files.FileDownloadError, match='unsafe_download_host'):
        with files.downloaded_epub(REFERENCE):
            pytest.fail('must not yield')
    assert network['connections'] == []
    assert not list(workspace_root.iterdir())


def test_mixed_dns_answers_refused(monkeypatch, network, workspace_root):
    monkeypatch.setattr(files.socket, 'getaddrinfo', lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))])
    with pytest.raises(files.FileDownloadError, match='unsafe_download_host'):
        with files.downloaded_epub(REFERENCE):
            pytest.fail('must not yield')
    assert network['connections'] == []
    assert not list(workspace_root.iterdir())


@pytest.mark.parametrize('declared', [str(files.MAX_INPUT_BYTES + 1), '9' * 5000])
def test_oversized_declared_length(network, workspace_root, declared):
    network['response'] = Response(headers={'Content-Length': declared})
    with pytest.raises(files.FileDownloadError, match='file_too_large'):
        with files.downloaded_epub(REFERENCE):
            pytest.fail('must not yield')
    assert network['response'].reads == []
    assert not list(workspace_root.iterdir())


@pytest.mark.parametrize('headers', [{}, {'Content-Length': '1'}])
def test_stream_limit_without_trusting_header(network, workspace_root, monkeypatch, headers):
    monkeypatch.setattr(files, 'MAX_INPUT_BYTES', 5)
    network['response'] = Response(chunks=(b'12345', b'6', b'NEVER'), headers=headers)
    with pytest.raises(files.FileDownloadError, match='file_too_large'):
        with files.downloaded_epub(REFERENCE):
            pytest.fail('must not yield')
    assert network['response'].reads == [6, 1]
    assert not list(workspace_root.iterdir())
    assert network['connections'][0].closed


def test_exact_limit_accepted(network, workspace_root, monkeypatch):
    monkeypatch.setattr(files, 'MAX_INPUT_BYTES', 5)
    network['response'] = Response(chunks=(b'12345',), headers={'Content-Length': '5'})
    with files.downloaded_epub(REFERENCE) as workspace:
        assert (workspace / 'input.epub').read_bytes() == b'12345'
    assert not list(workspace_root.iterdir())


@pytest.mark.parametrize('declared', ['-1', 'garbage', '1, 3', '２'])
def test_invalid_content_length(network, workspace_root, declared):
    network['response'] = Response(headers={'Content-Length': declared})
    with pytest.raises(files.FileDownloadError, match='Invalid Content-Length'):
        with files.downloaded_epub(REFERENCE):
            pytest.fail('must not yield')
    assert not list(workspace_root.iterdir())


def test_truncated_body(network, workspace_root):
    network['response'] = Response(chunks=(b'123',), headers={'Content-Length': '10'})
    with pytest.raises(files.FileDownloadError, match='Truncated'):
        with files.downloaded_epub(REFERENCE):
            pytest.fail('must not yield')
    assert not list(workspace_root.iterdir())


@pytest.mark.parametrize('status', [301, 302, 303, 307, 308])
def test_all_redirects_refused(network, workspace_root, status):
    network['response'] = Response(status=status, headers={'Location': 'http://127.0.0.1/?secret=LEAK'})
    with pytest.raises(files.FileDownloadError, match='download_redirect_refused') as caught:
        with files.downloaded_epub(REFERENCE):
            pytest.fail('must not yield')
    assert 'LEAK' not in str(caught.value)
    assert len(network['connections']) == 1
    assert not list(workspace_root.iterdir())


@pytest.mark.parametrize('status', [403, 404, 500])
def test_failed_download(network, workspace_root, status):
    network['response'] = Response(status=status)
    with pytest.raises(files.FileDownloadError, match='download_failed'):
        with files.downloaded_epub(REFERENCE):
            pytest.fail('must not yield')
    assert not list(workspace_root.iterdir())


@pytest.mark.parametrize('error', [TimeoutError('TOP_SECRET'), OSError('TOP_SECRET'),
                                   http.client.IncompleteRead(b'partial')])
def test_stream_exception_cleanup_and_redaction(network, workspace_root, error):
    network['response'] = Response(error=error)
    label = 'download_timeout' if isinstance(error, TimeoutError) else 'download_failed'
    with pytest.raises(files.FileDownloadError, match=label) as caught:
        with files.downloaded_epub(REFERENCE):
            pytest.fail('must not yield')
    assert 'TOP_SECRET' not in str(caught.value)
    assert network['connections'][0].closed
    assert network['response'].closed
    assert not list(workspace_root.iterdir())


def test_connection_timeout(network, workspace_root):
    network['request_error'] = TimeoutError('TOP_SECRET')
    with pytest.raises(files.FileDownloadError, match='download_timeout'):
        with files.downloaded_epub(REFERENCE):
            pytest.fail('must not yield')
    assert network['connections'][0].closed
    assert not list(workspace_root.iterdir())


def test_time_budget(network, workspace_root, monkeypatch):
    clock = iter([0, files.DOWNLOAD_BUDGET_SECONDS + 1])
    monkeypatch.setattr(files.time, 'monotonic', lambda: next(clock))
    with pytest.raises(files.FileDownloadError, match='download_timeout'):
        with files.downloaded_epub(REFERENCE):
            pytest.fail('must not yield')
    assert network['response'].reads == []
    assert not list(workspace_root.iterdir())


def test_compressed_transfer_refused(network, workspace_root):
    network['response'] = Response(headers={'Content-Encoding': 'gzip'})
    with pytest.raises(files.FileDownloadError, match='download_encoding_refused'):
        with files.downloaded_epub(REFERENCE):
            pytest.fail('must not yield')
    assert not list(workspace_root.iterdir())


def test_downstream_programming_error_still_cleans(network, workspace_root):
    with pytest.raises(TypeError, match='bug'):
        with files.downloaded_epub(REFERENCE) as workspace:
            raise TypeError('bug')
    assert not workspace.exists()
    assert not list(workspace_root.iterdir())


def test_pinned_connection_preserves_tls_hostname(monkeypatch):
    class Raw:
        def close(self):
            pass
    raw = Raw()
    calls = []
    monkeypatch.setattr(files.socket, 'create_connection', lambda address, timeout: calls.append((address, timeout)) or raw)
    class TLS:
        def wrap_socket(self, sock, server_hostname):
            calls.append((sock, server_hostname))
            return raw
    monkeypatch.setattr(files.ssl, 'create_default_context', lambda: TLS())
    connection = files._PinnedHTTPSConnection('uploads.example.org', '8.8.8.8')
    connection.connect()
    assert calls == [(('8.8.8.8', 443), files.DOWNLOAD_TIMEOUT_SECONDS), (raw, 'uploads.example.org')]
    assert connection.sock is raw


def test_dns_failure_clean(monkeypatch, workspace_root):
    def resolve(*args, **kwargs):
        raise socket.gaierror('TOP_SECRET')
    monkeypatch.setattr(files.socket, 'getaddrinfo', resolve)
    with pytest.raises(files.FileDownloadError, match='resolution failed') as caught:
        with files.downloaded_epub(REFERENCE):
            pytest.fail('must not yield')
    assert 'TOP_SECRET' not in str(caught.value)
    assert not list(workspace_root.iterdir())


def test_temporary_workspace_failure_clean(monkeypatch):
    def fail(**kwargs):
        raise PermissionError('sensitive path')
    monkeypatch.setattr(files.tempfile, 'TemporaryDirectory', fail)
    with pytest.raises(files.FileDownloadError, match='temporary_workspace_failed') as caught:
        with files.downloaded_epub(REFERENCE):
            pytest.fail('must not yield')
    assert 'sensitive path' not in str(caught.value)


def test_tls_failure_closes_raw_socket(monkeypatch):
    class Raw:
        closed = False
        def close(self):
            self.closed = True
    raw = Raw()
    monkeypatch.setattr(files.socket, 'create_connection', lambda *args, **kwargs: raw)
    class TLS:
        def wrap_socket(self, *args, **kwargs):
            raise files.ssl.SSLError('bad certificate')
    monkeypatch.setattr(files.ssl, 'create_default_context', lambda: TLS())
    with pytest.raises(files.ssl.SSLError):
        files._PinnedHTTPSConnection('uploads.example.org', '8.8.8.8').connect()
    assert raw.closed
