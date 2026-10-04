import asyncio
import json

import pytest
from mcp import StdioServerParameters
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from retail_agent.errors import ExternalToolError, ExternalToolUnavailableError
from retail_agent.mcp_client import McpConfigError, McpServerConfig, McpToolHub, load_mcp_server_configs


def _fake_server():
    """An in-process MCP server standing in for an external one — the mcp
    `Client` connects to it directly, so these tests spawn no subprocess."""
    server = MCPServer("fake")

    @server.tool(description="Add two numbers.")
    def add(a: int, b: int) -> int:
        return a + b

    @server.tool(description="Always fails.")
    def broken() -> str:
        raise ToolError("bad argument: x")

    @server.tool(description="Deletes everything.", annotations=ToolAnnotations(destructive_hint=True))
    def wipe() -> str:
        return "gone"

    @server.tool(description="Takes a while.")
    async def slow() -> str:
        await asyncio.sleep(5)
        return "done"

    return server


@pytest.fixture
def hub_factory():
    hubs = []

    def make(*servers, **kwargs):
        hub = McpToolHub(list(servers), **kwargs)
        hub.start()
        hubs.append(hub)
        return hub

    yield make
    for hub in hubs:
        hub.close()


# --- Discovery and filtering -----------------------------------------------------


def test_tools_are_namespaced_by_server(hub_factory):
    hub = hub_factory(McpServerConfig("fake", _fake_server()))

    names = {spec["name"] for spec in hub.tool_specs()}

    assert names == {"fake__add", "fake__broken", "fake__slow"}
    assert hub.has_tool("fake__add")
    assert not hub.has_tool("add")


def test_tool_spec_has_the_shape_bind_tools_expects(hub_factory):
    hub = hub_factory(McpServerConfig("fake", _fake_server()))

    spec = next(s for s in hub.tool_specs() if s["name"] == "fake__add")

    assert "fake" in spec["description"] and "Add two numbers." in spec["description"]
    assert set(spec["parameters"]["properties"]) == {"a", "b"}


def test_destructive_tool_is_skipped_by_default(hub_factory):
    hub = hub_factory(McpServerConfig("fake", _fake_server()))

    assert not hub.has_tool("fake__wipe")


def test_allowlist_loads_only_listed_tools_even_destructive_ones(hub_factory):
    hub = hub_factory(McpServerConfig("fake", _fake_server(), tools=("add", "wipe")))

    assert {spec["name"] for spec in hub.tool_specs()} == {"fake__add", "fake__wipe"}


def test_server_name_is_sanitized_for_gemini(hub_factory):
    hub = hub_factory(McpServerConfig("1 weird/name", _fake_server(), tools=("add",)))

    assert [spec["name"] for spec in hub.tool_specs()] == ["_1_weird_name__add"]


def test_unreachable_server_is_skipped_without_stopping_the_others(hub_factory):
    dead = McpServerConfig("dead", StdioServerParameters(command="/nonexistent/mcp-server"))
    hub = hub_factory(dead, McpServerConfig("fake", _fake_server()), connect_timeout_seconds=5)

    assert hub.server_count == 1
    assert hub.has_tool("fake__add")


# --- Calls ---------------------------------------------------------------------


def test_call_returns_the_tool_result(hub_factory):
    hub = hub_factory(McpServerConfig("fake", _fake_server()))

    payload = hub.call("fake__add", {"a": 2, "b": 3})

    assert "5" in json.dumps(payload)


def test_tool_error_raises_self_correctable_external_tool_error(hub_factory):
    hub = hub_factory(McpServerConfig("fake", _fake_server()))

    with pytest.raises(ExternalToolError, match="bad argument"):
        hub.call("fake__broken", {})


def test_call_timeout_raises_unavailable(hub_factory):
    hub = hub_factory(McpServerConfig("fake", _fake_server()), call_timeout_seconds=0.2)

    with pytest.raises(ExternalToolUnavailableError, match="timed out"):
        hub.call("fake__slow", {})


def test_call_after_session_closed_raises_unavailable():
    hub = McpToolHub([McpServerConfig("fake", _fake_server())])
    hub.start()
    hub.close()

    with pytest.raises(ExternalToolUnavailableError):
        hub.call("fake__add", {"a": 1, "b": 1})


# --- Config file ---------------------------------------------------------------


def test_config_reads_mcp_json_shape(tmp_path):
    path = tmp_path / "servers.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "time": {"command": "uvx", "args": ["mcp-server-time"], "tools": ["get_current_time"]},
                    "remote": {"url": "https://example.com/mcp"},
                }
            }
        )
    )

    time_server, remote = load_mcp_server_configs(path)

    assert time_server.name == "time"
    assert time_server.target.command == "uvx" and time_server.target.args == ["mcp-server-time"]
    assert time_server.tools == ("get_current_time",)
    assert remote.target == "https://example.com/mcp" and remote.tools is None


def test_config_missing_file_raises(tmp_path):
    with pytest.raises(McpConfigError, match="not found"):
        load_mcp_server_configs(tmp_path / "nope.json")


def test_config_invalid_json_raises(tmp_path):
    path = tmp_path / "servers.json"
    path.write_text("{not json")

    with pytest.raises(McpConfigError, match="not valid JSON"):
        load_mcp_server_configs(path)


def test_config_entry_without_command_or_url_raises(tmp_path):
    path = tmp_path / "servers.json"
    path.write_text(json.dumps({"mcpServers": {"bad": {"args": []}}}))

    with pytest.raises(McpConfigError, match="'command' or 'url'"):
        load_mcp_server_configs(path)
