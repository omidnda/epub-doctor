"""Local stdio MCP adapter. Paths are local to this process, never URLs/uploads."""

from lzma import LZMAError
from zipfile import BadZipFile, LargeZipFile
from zlib import error as ZlibError

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import service
from .operation_limit import ServerBusyError
from .validator import EPUBCheckError
from .zip_inspection import UnsafeZipError


mcp = MCPServer("EPUB Doctor")


@mcp.tool()
def inspect_epub(path: str) -> dict[str, object]:
    """Inspect a local EPUB read-only. Relative paths use the server working directory.

    Returns confirmed diagnoses, not a guarantee that repair will be accepted.
    URLs, uploads and encoded EPUB data are not supported.
    """
    try:
        return service.inspect_epub(path)
    except ServerBusyError as exc:
        raise ToolError(str(exc)) from None
    except FileNotFoundError as exc:
        raise ToolError(f"input_not_found: {exc}") from exc
    except EPUBCheckError as exc:
        raise ToolError(f"epubcheck_failure: {exc}") from exc
    except UnsafeZipError as exc:
        raise ToolError(f"{exc.reason}: {exc}") from exc
    except LargeZipFile as exc:
        raise ToolError(f"unsupported_zip64: {exc}") from exc
    except (BadZipFile, EOFError, UnicodeError, LZMAError, ZlibError) as exc:
        raise ToolError(f"invalid_zip: {exc}") from exc
    except OSError as exc:
        raise ToolError(f"input_read_failed: {exc}") from exc
    except NotImplementedError as exc:
        raise ToolError(f"unsupported_zip: {exc}") from exc


@mcp.tool()
def repair_epub(input_path: str, output_path: str) -> dict[str, object]:
    """Repair a local EPUB to a separate local output using the safe service pipeline.

    Relative paths use the server working directory. Refusals/failures are normal
    structured service results; inspect reason, final_valid and output_file.
    Existing inputs/outputs are never overwritten. No URLs or uploads.
    """
    try:
        return service.repair_epub_file(input_path, output_path)
    except ServerBusyError as exc:
        raise ToolError(str(exc)) from None


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
