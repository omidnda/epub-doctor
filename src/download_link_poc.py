"""Isolated development probe for clickable HTTPS links in the real ChatGPT UI.

Serves fixed public text only. No user files, repairs, storage, tokens or TTL.
The existing MCP adapters never register this probe.
"""

import argparse
import re
from urllib.parse import urlsplit

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import Response


PROBE_PATH = '/download-link-poc.txt'
PROBE_FILENAME = 'epub-doctor-link-poc.txt'
PROBE_BYTES = (
    b'EPUB Doctor HTTPS download-link proof of concept.\n'
    b'No EPUB or user data is stored or served.\n'
)


def _public_origin(value: str) -> str:
    """Validate operator configuration; never derive links from request headers."""
    try:
        value.encode('ascii')
        parsed = urlsplit(value)
        host = parsed.hostname or ''
        if (parsed.scheme != 'https' or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or parsed.path not in ('', '/')
                or '?' in value or '#' in value or len(value) > 1024
                or not re.fullmatch(r'[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+', host)
                or host.endswith('.localhost') or host == 'localhost'
                or re.fullmatch(r'[0-9.]+', host)
                or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)):
            raise ValueError
    except (ValueError, UnicodeError):
        raise ValueError('public origin must be an HTTPS DNS origin on port 443, without credentials, path or query') from None
    return value.rstrip('/')


def create_server(public_origin: str) -> MCPServer:
    """Build the standalone probe; import alone never starts a listener."""
    origin = _public_origin(public_origin)
    server = MCPServer('EPUB Doctor download-link PoC')

    @server.tool(annotations=ToolAnnotations(
        read_only_hint=True, destructive_hint=False, open_world_hint=False,
    ))
    async def get_download_link_poc() -> dict[str, str]:
        """Return a fixed public HTTPS text-file link for a development UI test.

        Use only to test whether ChatGPT displays a usable link. Does not repair
        EPUBs, receive uploads, retain user data or deliver a repaired EPUB.
        """
        return {
            'url': origin + PROBE_PATH,
            'file_name': PROBE_FILENAME,
            'detail': 'Development probe only: fixed public text, no EPUB or user data.',
        }

    @server.custom_route(PROBE_PATH, methods=['GET'])
    async def download_probe(request: Request) -> Response:
        return Response(PROBE_BYTES, media_type='text/plain', headers={
            'Content-Disposition': f'attachment; filename="{PROBE_FILENAME}"',
            'Cache-Control': 'no-store',
            'Referrer-Policy': 'no-referrer',
            'X-Content-Type-Options': 'nosniff',
        })

    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public-origin', required=True, help='Actual HTTPS origin of the development tunnel')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8001)
    args = parser.parse_args(argv)
    try:
        origin = _public_origin(args.public_origin)
    except ValueError as exc:
        parser.error(str(exc))
    server = create_server(origin)
    security = TransportSecuritySettings(
        allowed_hosts=['127.0.0.1:*', 'localhost:*', '[::1]:*', urlsplit(origin).netloc],
        allowed_origins=['http://127.0.0.1:*', 'http://localhost:*', 'http://[::1]:*', origin],
    )
    server.run(transport='streamable-http', host=args.host, port=args.port,
               streamable_http_path='/mcp', stateless_http=True, json_response=True,
               transport_security=security)


if __name__ == '__main__':
    main()
