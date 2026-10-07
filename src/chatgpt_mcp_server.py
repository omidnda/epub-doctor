"""ChatGPT file-input MCP adapter, with stdio and development Streamable HTTP.

Native repaired-file delivery is unavailable in the verified SDK/docs. A valid
output is tested within its temporary workspace, then deleted, never exposed as
an inaccessible filesystem path or invented file reference.
"""

import argparse
from typing import Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from typing_extensions import NotRequired, TypedDict

from . import mcp_server as local_mcp
from . import service
from .chatgpt_files import FileDownloadError, OpenAIFile, downloaded_epub


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
    status: Literal['unavailable']
    delivered: Literal[False]
    detail: str


class RepairResponse(TypedDict):
    repair: Repair
    file_output: FileOutput


OUTPUT_LIMITATION = (
    'Native ChatGPT file delivery is not implemented: no verified SDK/API to '
    'register generated EPUB bytes and return a ChatGPT-downloadable file reference. '
    'Any validated temporary output was deleted; no repaired file is available.'
)

mcp = MCPServer('EPUB Doctor — ChatGPT files')


@mcp.tool(meta={'openai/fileParams': ['file']},
          annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True))
def inspect_epub(file: OpenAIFile) -> Inspection:
    """Inspect the EPUB attached by the user using its ChatGPT file reference.

    Read-only validation/diagnosis; creates no output EPUB. Input is downloaded
    temporarily and deleted. Returns the unchanged service inspection result.
    """
    try:
        with downloaded_epub(file) as workspace:
            return local_mcp.inspect_epub(str(workspace / 'input.epub'))
    except FileDownloadError as exc:
        raise ToolError(str(exc)) from None


@mcp.tool(meta={'openai/fileParams': ['file']},
          annotations=ToolAnnotations(destructive_hint=False, open_world_hint=True))
def repair_epub(file: OpenAIFile) -> RepairResponse:
    """Attempt safe repair of the attached EPUB in a temporary workspace.

    LIMITATION: this version cannot deliver a repaired file to ChatGPT. Validated
    temporary output is deleted. Reports repair semantics and delivery status;
    never claim the user received a repaired EPUB. No output path is requested.
    """
    try:
        with downloaded_epub(file) as workspace:
            result = service.repair_epub_file(str(workspace / 'input.epub'), str(workspace / 'repaired.epub'))
            # A core output path is internal evidence, not a downloadable file.
            return {'repair': {**result, 'output_file': None},
                    'file_output': {'status': 'unavailable', 'delivered': False, 'detail': OUTPUT_LIMITATION}}
    except FileDownloadError as exc:
        raise ToolError(str(exc)) from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--transport', choices=('stdio', 'streamable-http'), default='stdio')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--allowed-host', action='append', default=[], help='Additional exact Host header, e.g. dev.example.org')
    parser.add_argument('--allowed-origin', action='append', default=[], help='Additional exact Origin, e.g. https://dev.example.org')
    args = parser.parse_args(argv)
    if args.transport == 'stdio':
        mcp.run(transport='stdio')
    else:
        security = TransportSecuritySettings(
            allowed_hosts=['127.0.0.1:*', 'localhost:*', '[::1]:*', *args.allowed_host],
            allowed_origins=['http://127.0.0.1:*', 'http://localhost:*', 'http://[::1]:*', *args.allowed_origin],
        )
        mcp.run(transport='streamable-http', host=args.host, port=args.port,
                streamable_http_path='/mcp', stateless_http=True, json_response=True,
                transport_security=security)


if __name__ == '__main__':
    main()
