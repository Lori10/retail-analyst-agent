from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client

from retail_agent.errors import ExternalToolError, ExternalToolUnavailableError

logger = logging.getLogger(__name__)

DEFAULT_CONNECT_TIMEOUT_SECONDS = 30.0
DEFAULT_CALL_TIMEOUT_SECONDS = 60.0

# Gemini function names: start with a letter or underscore, then letters,
# digits, underscores, dots or dashes, at most 64 characters.
_INVALID_NAME_CHARS = re.compile(r"[^A-Za-z0-9_.-]")
_MAX_TOOL_NAME_LENGTH = 64


class McpConfigError(Exception):
    """Raised by `load_mcp_server_configs` when the server list file is
    missing or malformed. A startup failure, like `ConfigError` — the user
    explicitly pointed `AGENT_MCP_SERVERS` at it, so a broken file is
    reported rather than silently ignored."""


@dataclass(frozen=True)
class McpServerConfig:
    """One external MCP server the agent should connect to.

    Attributes:
        name: The server's key in the config file; prefixes its tool names.
        target: What `mcp.Client` connects to — `StdioServerParameters` for a
            subprocess, a URL string for Streamable HTTP, or (in tests) an
            in-process `MCPServer`.
        tools: Optional allowlist of the server's own tool names. `None`
            means every tool not annotated as destructive.
    """

    name: str
    target: Any
    tools: tuple[str, ...] | None = None


def load_mcp_server_configs(path: str | Path) -> list[McpServerConfig]:
    """Read a server list in the same JSON shape as Claude Code's `.mcp.json`.

    `{"mcpServers": {"<name>": {"command": ..., "args": [...], "env": {...}}}}`
    for a stdio server, or `{"url": ...}` for Streamable HTTP. Each entry may
    also carry `"tools": [...]`, an allowlist of that server's tool names.

    Args:
        path: The JSON file to read.

    Returns:
        One `McpServerConfig` per entry, in file order.

    Raises:
        McpConfigError: The file is missing, isn't valid JSON, or an entry
            has neither `command` nor `url`.
    """
    try:
        data = json.loads(Path(path).read_text())
    except FileNotFoundError as exc:
        raise McpConfigError(f"MCP server config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise McpConfigError(f"MCP server config file {path} is not valid JSON: {exc}") from exc

    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        raise McpConfigError(f"MCP server config file {path} has no 'mcpServers' object")

    configs = []
    for name, entry in servers.items():
        if not isinstance(entry, dict):
            raise McpConfigError(f"MCP server '{name}' must be an object")
        if "command" in entry:
            target = StdioServerParameters(
                command=entry["command"],
                args=entry.get("args", []),
                env=entry.get("env"),
                cwd=entry.get("cwd"),
            )
        elif "url" in entry:
            target = entry["url"]
        else:
            raise McpConfigError(f"MCP server '{name}' needs either 'command' or 'url'")
        tools = entry.get("tools")
        configs.append(McpServerConfig(name=name, target=target, tools=tuple(tools) if tools is not None else None))
    return configs


def _tool_name(server_name: str, tool_name: str) -> str:
    """Namespace and sanitize an external tool's name for Gemini.

    Namespacing as `<server>__<tool>` keeps two servers' same-named tools
    apart and can never collide with a built-in tool (none contains `__`).
    """
    name = _INVALID_NAME_CHARS.sub("_", f"{server_name}__{tool_name}")
    if not re.match(r"[A-Za-z_]", name):
        name = f"_{name}"
    return name[:_MAX_TOOL_NAME_LENGTH]


def _gemini_accepts(spec: dict) -> bool:
    """Whether Gemini's function-declaration conversion accepts `spec`.

    Checked per tool so one external schema Gemini can't represent (an
    unresolved `$ref`, say) is skipped on its own instead of failing
    `bind_tools` for every tool, built-ins included.
    """
    from langchain_google_genai._function_utils import convert_to_genai_function_declarations

    try:
        convert_to_genai_function_declarations([spec])
    except Exception as exc:
        logger.warning("mcp_tool_skipped", extra={"tool": spec["name"], "reason": f"unsupported schema: {exc}"})
        return False
    return True


def _result_text(result) -> str:
    """Join a `CallToolResult`'s text blocks; note any non-text block."""
    parts = [
        block.text if getattr(block, "type", None) == "text" else f"[{getattr(block, 'type', 'unknown')} content omitted]"
        for block in result.content
    ]
    return "\n".join(parts)


@dataclass
class _Connection:
    """A live session to one server, held open by `task` until `stop` is set."""

    client: Any
    stop: asyncio.Event
    task: asyncio.Task
    tools: list = field(default_factory=list)


class McpToolHub:
    """Connects the agent to external MCP servers and exposes their tools.

    The graph is synchronous; the MCP client is async. The hub owns one
    background thread running an asyncio loop that holds every server's
    session open for the process lifetime (a stdio server is a long-lived
    subprocess), and bridges each sync call onto that loop.

    Each session lives inside one long-running task per server, because
    `mcp.Client`'s context manager is anyio-based and must be entered and
    exited in the same task.
    """

    def __init__(
        self,
        servers: list[McpServerConfig],
        *,
        connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        call_timeout_seconds: float = DEFAULT_CALL_TIMEOUT_SECONDS,
    ) -> None:
        """Args:
        servers: The servers to connect to on `start()`.
        connect_timeout_seconds: Per-server limit for connecting and
            listing tools.
        call_timeout_seconds: Per-call limit for a tool call.
        """
        self._servers = servers
        self._connect_timeout = connect_timeout_seconds
        self._call_timeout = call_timeout_seconds
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="mcp-client", daemon=True)
        self._connections: dict[str, _Connection] = {}
        # exposed name -> (server name, the server's own tool name)
        self._routes: dict[str, tuple[str, str]] = {}
        self._specs: list[dict] = []
        # An external server's own stderr would interleave with the chat in
        # the terminal; it's only worth seeing when debugging.
        self._errlog = sys.stderr if logger.isEnabledFor(logging.DEBUG) else open(os.devnull, "w")

    def start(self) -> None:
        """Connect to every configured server and collect its tools.

        A server that fails to connect or list its tools is logged and
        skipped — the agent still starts with its built-in tools and every
        other server, the same way a missing LangSmith key degrades.
        """
        self._thread.start()
        for server in self._servers:
            future = asyncio.run_coroutine_threadsafe(self._connect(server), self._loop)
            try:
                self._connections[server.name] = future.result(timeout=self._connect_timeout + 5)
            except Exception as exc:
                future.cancel()
                logger.warning("mcp_server_unavailable", extra={"mcp_server": server.name, "reason": repr(exc)})
                continue
            self._register_tools(server, self._connections[server.name].tools)

    async def _connect(self, server: McpServerConfig) -> _Connection:
        ready = asyncio.Event()
        stop = asyncio.Event()
        holder: dict[str, Any] = {}

        async def hold_session() -> None:
            target = server.target
            if isinstance(target, StdioServerParameters):
                target = stdio_client(target, errlog=self._errlog)
            async with Client(target) as client:
                holder["client"] = client
                ready.set()
                await stop.wait()

        task = asyncio.create_task(hold_session())
        ready_wait = asyncio.create_task(ready.wait())
        await asyncio.wait({task, ready_wait}, timeout=self._connect_timeout, return_when=asyncio.FIRST_COMPLETED)
        if not ready.is_set():
            ready_wait.cancel()
            if task.done():
                raise task.exception() or RuntimeError("session closed during connect")
            task.cancel()
            raise TimeoutError(f"no handshake within {self._connect_timeout}s")

        client = holder["client"]
        tools = []
        cursor = None
        while True:
            page = await client.list_tools(cursor=cursor)
            tools.extend(page.tools)
            cursor = page.next_cursor
            if not cursor:
                break
        return _Connection(client=client, stop=stop, task=task, tools=tools)

    def _register_tools(self, server: McpServerConfig, tools: list) -> None:
        """Filter a server's tools and build their Gemini specs.

        With an allowlist, only listed tools load. Without one, every tool
        loads except those annotated `destructiveHint: true`: only
        `delete_reports` gets a human confirmation step in this agent, so a
        destructive external action isn't let in by default. Annotations are
        the server's own hints, not a guarantee — the allowlist is the hard
        control (docs/implementation-notes.md).
        """
        for tool in tools:
            if server.tools is not None:
                if tool.name not in server.tools:
                    continue
            elif tool.annotations is not None and tool.annotations.destructive_hint is True:
                logger.warning(
                    "mcp_tool_skipped", extra={"mcp_server": server.name, "tool": tool.name, "reason": "destructive"}
                )
                continue

            name = _tool_name(server.name, tool.name)
            if name in self._routes:
                logger.warning("mcp_tool_skipped", extra={"tool": name, "reason": "duplicate name"})
                continue
            spec = {
                "name": name,
                "description": f"[External tool from MCP server '{server.name}'] {tool.description or tool.name}",
                "parameters": tool.input_schema or {"type": "object", "properties": {}},
            }
            if not _gemini_accepts(spec):
                continue
            self._routes[name] = (server.name, tool.name)
            self._specs.append(spec)

    @property
    def server_count(self) -> int:
        """How many servers connected successfully."""
        return len(self._connections)

    def tool_specs(self) -> list[dict]:
        """Tool schemas in the same shape as `tools.py`'s, for `bind_tools`."""
        return list(self._specs)

    def has_tool(self, name: str) -> bool:
        """Whether `name` is an external tool this hub can dispatch."""
        return name in self._routes

    def call(self, name: str, args: dict) -> dict:
        """Call an external tool and return a JSON-safe payload.

        Args:
            name: The exposed (namespaced) tool name.
            args: The model-supplied arguments.

        Returns:
            `{"result": ...}` — the tool's structured content if it returned
            any, otherwise its text content.

        Raises:
            ExternalToolError: The server reported a tool error.
            ExternalToolUnavailableError: The session is gone, the call
                failed at the protocol level, or it timed out.
        """
        server_name, tool_name = self._routes[name]
        connection = self._connections[server_name]
        if connection.task.done():
            raise ExternalToolUnavailableError(f"MCP server '{server_name}' is no longer connected")

        future = asyncio.run_coroutine_threadsafe(connection.client.call_tool(tool_name, args), self._loop)
        try:
            result = future.result(timeout=self._call_timeout)
        except FutureTimeoutError as exc:
            future.cancel()
            raise ExternalToolUnavailableError(
                f"MCP tool '{name}' timed out after {self._call_timeout}s"
            ) from exc
        except Exception as exc:
            raise ExternalToolUnavailableError(f"MCP tool '{name}' failed: {exc}") from exc

        if result.is_error:
            raise ExternalToolError(_result_text(result) or f"MCP tool '{name}' reported an error")
        if result.structured_content is not None:
            return {"result": result.structured_content}
        return {"result": _result_text(result)}

    def close(self) -> None:
        """Close every session and stop the background loop."""
        if not self._thread.is_alive():
            return

        async def shutdown() -> None:
            for connection in self._connections.values():
                connection.stop.set()
            tasks = [c.task for c in self._connections.values()]
            if tasks:
                await asyncio.wait(tasks, timeout=5)

        try:
            asyncio.run_coroutine_threadsafe(shutdown(), self._loop).result(timeout=10)
        except Exception as exc:
            logger.warning("mcp_shutdown_failed", extra={"reason": repr(exc)})
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        if self._errlog is not sys.stderr:
            self._errlog.close()
