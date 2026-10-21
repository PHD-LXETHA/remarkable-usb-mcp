"""Official SDK client/server protocol integration over generated data."""

import asyncio

from mcp import Client

from remarkable_usb_mcp.server import create_server


class SyntheticDevice:
    def check_connection(self):
        return {"connected": True, "transport": "USB", "synthetic": True}

    def list_documents(self, **kwargs):
        return [{"uuid": "11111111-2222-4333-8444-555555555555", "name": "Synthetic document"}]

    def export_rmdoc(self, identifier):
        return {"uuid": identifier, "synthetic": True}


def test_official_client_discovers_and_calls_real_mcp_tools():
    async def exercise():
        async with Client(create_server(SyntheticDevice())) as client:
            tools = (await client.list_tools()).tools
            assert {tool.name for tool in tools} == {
                "check_connection",
                "list_documents",
                "export_rmdoc",
            }
            annotations = {tool.name: tool.annotations for tool in tools}
            assert annotations["check_connection"].read_only_hint
            assert not annotations["export_rmdoc"].read_only_hint
            result = await client.call_tool("list_documents", {"limit": 1})
            assert result.structured_content["total"] == 1
            assert result.structured_content["documents"][0]["name"] == "Synthetic document"

    asyncio.run(exercise())


def test_invalid_protocol_arguments_return_tool_error():
    async def exercise():
        async with Client(create_server(SyntheticDevice())) as client:
            result = await client.call_tool("list_documents", {"limit": 0})
            assert result.is_error

    asyncio.run(exercise())


def test_actual_stdio_handshake_needs_no_device_access(tmp_path):
    import sys

    from mcp import StdioServerParameters

    (tmp_path / "synthetic-key").write_text("SYNTHETIC-NOT-A-PRIVATE-KEY")
    (tmp_path / "synthetic-hosts").write_text("SYNTHETIC-NOT-A-HOST-IDENTITY")
    config = tmp_path / "local.toml"
    config.write_text(
        'identity_file="synthetic-key"\nknown_hosts_file="synthetic-hosts"\nexport_dir="out"\n'
    )

    async def exercise():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "remarkable_usb_mcp.server", "--config", str(config)],
        )
        async with Client(parameters, read_timeout_seconds=15) as client:
            tools = (await client.list_tools()).tools
            assert len(tools) == 3
            result = await client.call_tool("export_rmdoc", {"document_uuid": "invalid"})
            assert result.is_error

    asyncio.run(exercise())
