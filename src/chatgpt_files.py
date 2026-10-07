"""Official ChatGPT file-reference contract and bounded temporary HTTPS download.

The MCP SDK has no built-in OpenAIFile class. This TypedDict describes exactly
OpenAI's documented fileParams schema, not a second file-transfer protocol.
"""

import http.client
import ipaddress
import re
import socket
import ssl
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from typing_extensions import NotRequired, TypedDict


MAX_INPUT_BYTES = 20 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 15
DOWNLOAD_BUDGET_SECONDS = 60
DOWNLOAD_CHUNK_BYTES = 64 * 1024


class OpenAIFile(TypedDict):
    download_url: str
    file_id: str
    mime_type: NotRequired[str]
    file_name: NotRequired[str]


class FileDownloadError(ValueError):
    """Expected input/transfer failure, with a safe message free of signed URLs."""


def _validate_reference(file: OpenAIFile):
    if not isinstance(file, dict):
        raise FileDownloadError('invalid_file_reference: Expected a file object')
    for field in ('download_url', 'file_id'):
        if not isinstance(file.get(field), str) or not file[field].strip():
            raise FileDownloadError(f'invalid_file_reference: Missing or invalid {field}')
    for field in ('mime_type', 'file_name'):
        if field in file and not isinstance(file[field], str):
            raise FileDownloadError(f'invalid_file_reference: Invalid {field}')
    url = file['download_url']
    if len(url) > 16384 or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url):
        raise FileDownloadError('invalid_download_url: Invalid URL syntax')
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or '#' in url or parsed.port not in (None, 443)
                or '\\' in url):
            raise ValueError('Unsafe HTTPS URL')
        host = parsed.hostname.encode('idna').decode('ascii')
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', host):
                raise ValueError('Invalid host')
        target = parsed.path or '/'
        if parsed.query:
            target += '?' + parsed.query
        target.encode('ascii')  # Require a properly encoded HTTP request target.
    except (ValueError, UnicodeError):
        raise FileDownloadError('invalid_download_url: Only credential-free HTTPS URLs on port 443 are accepted') from None
    return host, target


def _public_address(host: str) -> str:
    """Resolve once and refuse the host if ANY answer is not a public address."""
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise FileDownloadError('download_failed: Host resolution failed') from None
    if not addresses:
        raise FileDownloadError('download_failed: No host addresses')
    for _, _, _, _, address in addresses:
        ip = ipaddress.ip_address(address[0])
        # IPv4-mapped IPv6 answers must also pass their IPv4 policy.
        mapped = getattr(ip, 'ipv4_mapped', None)
        if (not ip.is_global or ip.is_multicast or ip.is_reserved
                or getattr(ip, 'scope_id', None) is not None
                or getattr(ip, 'sixtofour', None) is not None
                or getattr(ip, 'teredo', None) is not None
                or (mapped is not None and not mapped.is_global)):
            raise FileDownloadError('unsafe_download_host: Non-public destination refused')
    return addresses[0][4][0]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Pin the verified address while retaining original-host TLS/SNI checks."""

    def __init__(self, host: str, address: str):
        self.address = address
        self.tls_context = ssl.create_default_context()
        super().__init__(host, timeout=DOWNLOAD_TIMEOUT_SECONDS, context=self.tls_context)

    def connect(self):
        raw = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            self.sock = self.tls_context.wrap_socket(raw, server_hostname=self.host)
        except (OSError, ValueError):
            raw.close()
            raise


def _download(host: str, target: str, destination: Path):
    started = time.monotonic()
    address = _public_address(host)
    connection = None
    try:
        connection = _PinnedHTTPSConnection(host, address)
        connection.request('GET', target, headers={'Accept-Encoding': 'identity'})
        with connection.getresponse() as response:
            if 300 <= response.status < 400:
                raise FileDownloadError('download_redirect_refused: Redirects are not followed')
            if response.status != 200:
                raise FileDownloadError(f'download_failed: HTTP status {response.status}')
            encoding = response.getheader('Content-Encoding', 'identity').lower()
            if encoding != 'identity':
                raise FileDownloadError('download_encoding_refused: Compressed HTTP transfers are unsupported')
            declared = response.getheader('Content-Length')
            if declared is not None:
                if not declared.isascii() or not declared.isdigit():
                    raise FileDownloadError('download_failed: Invalid Content-Length')
                if len(declared) > 12 or int(declared) > MAX_INPUT_BYTES:
                    raise FileDownloadError('file_too_large: Input exceeds 20 MiB')
            received = 0
            with destination.open('xb') as output:
                while True:
                    if time.monotonic() - started > DOWNLOAD_BUDGET_SECONDS:
                        raise FileDownloadError('download_timeout: Download time budget exceeded')
                    chunk = response.read1(min(DOWNLOAD_CHUNK_BYTES, MAX_INPUT_BYTES - received + 1))
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > MAX_INPUT_BYTES:
                        raise FileDownloadError('file_too_large: Input exceeds 20 MiB')
                    output.write(chunk)
            if declared is not None and received != int(declared):
                raise FileDownloadError('download_failed: Truncated or inconsistent Content-Length')
    except TimeoutError:
        raise FileDownloadError('download_timeout: HTTPS connection/read timed out') from None
    except (OSError, http.client.HTTPException):
        raise FileDownloadError('download_failed: HTTPS transfer failed') from None
    finally:
        if connection is not None:
            connection.close()


@contextmanager
def downloaded_epub(file: OpenAIFile):
    """Yield a private workspace containing input.epub; always remove it on exit.

    file_name/mime_type are display hints only. EPUBCheck determines validity.
    No URL, file ID or user-provided filename is used as a filesystem path.
    """
    host, target = _validate_reference(file)
    try:
        with tempfile.TemporaryDirectory(prefix='epub-doctor-chatgpt-') as directory:
            workspace = Path(directory)
            _download(host, target, workspace / 'input.epub')
            yield workspace
    except OSError:
        raise FileDownloadError('temporary_workspace_failed: Could not create/use/clean the temporary workspace') from None
