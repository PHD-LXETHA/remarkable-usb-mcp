"""Standard MCP stdio server over the reusable USB module."""

from __future__ import annotations

import argparse
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__
from .config import Config, UsbError
from .device import UsbDevice


def create_server(device: UsbDevice) -> MCPServer:
    server = MCPServer(
        "reMarkable USB",
        version=__version__,
        instructions="Read-only device access over verified USB only.",
    )
    read_only = ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )

    def invoke(operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except UsbError as error:
            raise ToolError(str(error)) from error

    @server.tool(annotations=read_only, structured_output=True)
    def check_connection() -> dict[str, Any]:
        """Verify the USB route, authenticated SSH connection and device architecture."""
        return invoke(device.check_connection)

    @server.tool(annotations=read_only, structured_output=True)
    def list_documents(
        offset: int = 0, limit: int = 100, include_trash: bool = False
    ) -> dict[str, Any]:
        """List device document/folder metadata; no document body is returned."""
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("Use a nonnegative offset and a limit between1 and200")
        entries = invoke(device.list_documents, include_trash=include_trash)
        return {
            "documents": entries[offset : offset + limit],
            "total": len(entries),
            "next_offset": offset + limit if offset + limit < len(entries) else None,
        }

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
        ),
    )
    def export_rmdoc(document_uuid: str) -> dict[str, Any]:
        """Read one explicit UUID over USB and save a new local RMDOC.

        The device is unchanged. Writes a new local file; never overwrites an existing file.
        Every member is checked against the device's stable before/after SHA256 manifest.
        """
        return invoke(device.export_rmdoc, document_uuid)

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="USB-only reMarkable MCP stdio server")
    parser.add_argument(
        "--config", required=True, help="Path to an operator-owned TOML configuration"
    )
    options = parser.parse_args()
    create_server(UsbDevice(Config.load(options.config))).run(transport="stdio")


if __name__ == "__main__":
    main()
