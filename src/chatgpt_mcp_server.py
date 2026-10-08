"""ChatGPT file-input MCP adapter with optional temporary HTTPS output links."""

import argparse
import asyncio
import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import anyio
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from typing_extensions import NotRequired, TypedDict
from starlette.responses import Response, StreamingResponse
from starlette.background import BackgroundTask
from starlette.requests import ClientDisconnect

from . import mcp_server as local_mcp
from . import service, validator
from .operation_limit import ServerBusyError, operation_slot
from .chatgpt_files import FileDownloadError, OpenAIFile, downloaded_epub
from .temporary_downloads import (
    CLEANUP_INTERVAL_SECONDS, DEFAULT_STORAGE_ROOT, MAX_DOWNLOAD_SECONDS, DownloadStoreError,
    TemporaryDownloadStore, public_origin,
)


class Inspection(TypedDict):
    valid: bool
    repairable: bool
    safe_repairs: list[str]
    fatal_count: int
    error_count: int
    warning_count: int
    messages: list[dict]
    publication: dict


class Repair(TypedDict):
    original_valid: bool | None
    detected: list[str]
    repaired: list[str]
    final_valid: bool | None
    output_file: None
    remaining_messages: list[dict]
    reason: str | None
    detail: NotRequired[str]


class FileOutput(TypedDict):
    status: Literal['unavailable', 'available', 'failed']
    delivered: bool
    detail: NotRequired[str]
    download_url: NotRequired[str]
    expires_in_seconds: NotRequired[int]
    file_name: NotRequired[str]


class RepairResponse(TypedDict):
    repair: Repair
    file_output: FileOutput


OUTPUT_LIMITATION = (
    'Temporary HTTPS delivery is not configured for this server. '
    'Any validated workspace output was deleted; no repaired file is available. '
    'This adapter does not deliver native ChatGPT attachments.'
)

def inspect_epub(file: OpenAIFile) -> Inspection:
    """Inspect the EPUB attached by the user using its ChatGPT file reference.

    Read-only validation/diagnosis; creates no output EPUB. Input is downloaded
    temporarily and deleted. Returns the unchanged service inspection result.
    """
    try:
        with operation_slot(), downloaded_epub(file) as workspace:
            return local_mcp.inspect_epub(str(workspace / 'input.epub'))
    except (FileDownloadError, ServerBusyError) as exc:
        raise ToolError(str(exc)) from None


def repair_epub(file: OpenAIFile) -> RepairResponse:
    """Attempt safe repair of the attached EPUB in a temporary workspace.

    When HTTPS delivery is configured, a successful validated repair returns an
    external temporary download link, not a native ChatGPT attachment. Present
    file_output.download_url as a clickable download link only if delivered is
    true. Anyone with that link may download for 15 minutes. No output path is
    requested. Input and workspace are deleted after processing.
    """
    return _repair_epub(file)


def _repair_epub(file, store=None, origin=None):
    try:
        with operation_slot(), downloaded_epub(file) as workspace:
            output = workspace / 'repaired.epub'
            result = service.repair_epub_file(str(workspace / 'input.epub'), str(output))
            delivery = {'status': 'unavailable', 'delivered': False, 'detail': OUTPUT_LIMITATION}
            if store is not None:
                delivery['detail'] = 'No successful validated repaired output is available.'
                if (result['final_valid'] is True and result['repaired'] and result['reason'] is None
                        and result['output_file'] == str(output)):
                    try:
                        published = store.publish(output, file.get('file_name'))
                        delivery = dict(status='available', delivered=True,
                                        download_url=origin + '/download/' + published.token,
                                        expires_in_seconds=published.expires_in_seconds, file_name=published.file_name)
                    except DownloadStoreError as exc:
                        delivery = dict(status='failed', delivered=False, detail=str(exc))
            # Core semantics are unchanged; its internal output path is never public.
            return {'repair': {**result, 'output_file': None},
                    'file_output': delivery}
    except (FileDownloadError, ServerBusyError) as exc:
        raise ToolError(str(exc)) from None


DOWNLOAD_HEADERS = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                    'X-Content-Type-Options': 'nosniff'}


class _DownloadResponse(StreamingResponse):
    """Bound stalled transfers and close even before the iterator starts."""
    def __init__(self, *args, stream, **kwargs):
        self._stream = stream
        super().__init__(*args, **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            with anyio.move_on_after(MAX_DOWNLOAD_SECONDS):
                await super().__call__(scope, receive, send)
        except ClientDisconnect:
            pass
        finally:
            self._stream.close()


def create_server(*, store=None, origin=None):
    """Exactly two tools; configured HTTP servers also have a capability route."""
    if (store is None) != (origin is None):
        raise ValueError('store and origin must be configured together')
    if origin is not None:
        origin = public_origin(origin)
    server = MCPServer('EPUB Doctor — ChatGPT files')
    server.tool(meta={'openai/fileParams': ['file']}, annotations=ToolAnnotations(
        read_only_hint=True, destructive_hint=False, open_world_hint=True))(inspect_epub)

    def configured_repair(file: OpenAIFile) -> RepairResponse:
        return _repair_epub(file, store, origin)
    configured_repair.__name__ = 'repair_epub'
    configured_repair.__doc__ = repair_epub.__doc__
    server.tool(meta={'openai/fileParams': ['file']}, annotations=ToolAnnotations(
        read_only_hint=False, destructive_hint=False, open_world_hint=True))(configured_repair)

    if store is not None:
        @server.custom_route('/download/{token}', methods=['GET'])
        async def download(request):
            try:
                opened = await anyio.to_thread.run_sync(store.open, request.path_params['token'])
            except DownloadStoreError:
                return Response(status_code=404, headers=DOWNLOAD_HEADERS)

            async def chunks():
                try:
                    remaining = opened.size
                    while remaining:
                        chunk = await anyio.to_thread.run_sync(opened.stream.read, min(64 * 1024, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk
                finally:
                    opened.stream.close()

            return _DownloadResponse(chunks(), stream=opened.stream, media_type='application/epub+zip', headers={
                **DOWNLOAD_HEADERS, 'Content-Length': str(opened.size),
                'Content-Disposition': f'attachment; filename="{opened.file_name}"',
            }, background=BackgroundTask(opened.stream.close))
    return server


mcp = create_server()


def create_download_app(store, origin, *, host='127.0.0.1', security=None):
    """Use the official SDK ASGI app and its lifespan, adding one cleanup task."""
    configured = create_server(store=store, origin=origin)
    app = configured.streamable_http_app(stateless_http=True, json_response=True,
                                         host=host, transport_security=security)
    app.state.ready = False

    async def health(request):
        # Lightweight readiness only: never execute Java or acquire processing capacity.
        ready = (app.state.ready and store._root_fd is not None
                 and validator.EPUBCHECK_JAR.is_file() and shutil.which('java') is not None)
        return Response('ok' if ready else 'unavailable', status_code=200 if ready else 503,
                        media_type='text/plain', headers=DOWNLOAD_HEADERS)

    app.add_route('/healthz', health, methods=['GET'])
    original_lifespan = app.router.lifespan_context

    async def sweep():
        while True:
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            try:
                await asyncio.to_thread(store.cleanup)
            except OSError:
                logging.getLogger(__name__).warning('Temporary output cleanup failed; retrying next interval')

    @asynccontextmanager
    async def lifespan(app):
        store.cleanup()
        async with original_lifespan(app):
            task = asyncio.create_task(sweep())
            app.state.ready = True
            try:
                yield
            finally:
                app.state.ready = False
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
    app.router.lifespan_context = lifespan
    return app


def _run_download_http(store, origin, host, port, security):
    # SDK's convenience runner enables access logs. Its supported ASGI API lets
    # us disable them so request paths containing bearer tokens are never logged.
    import uvicorn
    app = create_download_app(store, origin, host=host, security=security)
    uvicorn.Server(uvicorn.Config(app, host=host, port=port, access_log=False, log_level='info')).run()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--transport', choices=('stdio', 'streamable-http'), default='stdio')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--allowed-host', action='append', default=[], help='Additional exact Host header, e.g. dev.example.org')
    parser.add_argument('--allowed-origin', action='append', default=[], help='Additional exact Origin, e.g. https://dev.example.org')
    parser.add_argument('--public-origin', help='Explicit HTTPS origin; enables temporary output downloads over HTTP')
    parser.add_argument('--download-directory', type=Path, default=DEFAULT_STORAGE_ROOT,
                        help='Dedicated private output directory, owned by this process user (mode 0700)')
    args = parser.parse_args(argv)
    origin = None
    if args.public_origin:
        if args.transport != 'streamable-http':
            parser.error('--public-origin requires --transport streamable-http')
        try:
            origin = public_origin(args.public_origin)
        except ValueError as exc:
            parser.error(str(exc))
    if args.transport == 'stdio':
        mcp.run(transport='stdio')
    else:
        security = TransportSecuritySettings(
            allowed_hosts=['127.0.0.1:*', 'localhost:*', '[::1]:*', *args.allowed_host,
                           *([urlsplit(origin).netloc] if origin else [])],
            allowed_origins=['http://127.0.0.1:*', 'http://localhost:*', 'http://[::1]:*', *args.allowed_origin,
                             *([origin] if origin else [])],
        )
        if origin:
            try:
                store = TemporaryDownloadStore(args.download_directory)
            except DownloadStoreError as exc:
                parser.error(str(exc))
            try:
                _run_download_http(store, origin, args.host, args.port, security)
            finally:
                store.close()
            return
        mcp.run(transport='streamable-http', host=args.host, port=args.port,
                streamable_http_path='/mcp', stateless_http=True, json_response=True,
                transport_security=security)


if __name__ == '__main__':
    main()
