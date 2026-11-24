"""The copilot graph reaches the reliability tools through MCP, via langchain-mcp-adapters.

One MCP client session stays open on a background event loop (so tool calls don't respawn a server each time):
- transport "memory" (default): the GhostBus MCP server runs in-process, connected over MCP memory streams;
  the full MCP protocol (initialize, list_tools, call_tool) still runs.
- transport "stdio": the server runs as a subprocess (`python -m gbcopilot.mcp_server`), as an external MCP client
  would use it.
Set COPILOT_MCP_TRANSPORT to choose.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from contextlib import AsyncExitStack

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.tools import load_mcp_tools


class McpToolError(RuntimeError):
    pass


class McpToolHost:
    def __init__(self, transport: str | None = None):
        self.transport = transport or os.environ.get("COPILOT_MCP_TRANSPORT", "memory")
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, name="mcp-host", daemon=True)
        self._thread.start()
        self._stack: AsyncExitStack | None = None
        self.tools: dict[str, BaseTool] = self._submit(self._open(), timeout=60)

    def _submit(self, coro, timeout: float = 30):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=timeout)

    async def _open(self) -> dict[str, BaseTool]:
        self._stack = AsyncExitStack()
        if self.transport == "stdio":
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            params = StdioServerParameters(command=sys.executable, args=["-m", "gbcopilot.mcp_server"],
                                           env={**os.environ})
            read, write = await self._stack.enter_async_context(stdio_client(params))
            session = await self._stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        else:
            from mcp.shared.memory import create_connected_server_and_client_session

            from .mcp_server import mcp

            session = await self._stack.enter_async_context(create_connected_server_and_client_session(mcp))
        tools = await load_mcp_tools(session)
        return {t.name: t for t in tools}

    def call(self, name: str, args: dict) -> dict:
        if name not in self.tools:
            raise McpToolError(f"unknown tool {name}")
        try:
            out = self._submit(self.tools[name].ainvoke(args))
        except Exception as e:  # noqa: BLE001
            raise McpToolError(str(e)[:300]) from None
        return parse_tool_output(out)

    def close(self) -> None:
        if self._stack is not None:
            try:
                self._submit(self._stack.aclose(), timeout=10)
            except Exception:  # noqa: BLE001
                pass
        self.loop.call_soon_threadsafe(self.loop.stop)


def parse_tool_output(out) -> dict:
    """langchain-mcp-adapters returns text content (and artifacts); the GhostBus tools return one JSON object."""
    if isinstance(out, tuple):
        out = out[0]
    if isinstance(out, list):
        texts = [b.get("text", "") if isinstance(b, dict) else getattr(b, "text", str(b)) for b in out]
        out = "".join(texts)
    if isinstance(out, dict):
        return out
    s = str(out)
    try:
        val = json.loads(s)
    except json.JSONDecodeError:
        raise McpToolError(s[:300]) from None
    if isinstance(val, dict) and "result" in val and len(val) == 1:
        val = val["result"]
    return val


_HOST: McpToolHost | None = None
_HOST_LOCK = threading.Lock()


def host() -> McpToolHost:
    """One shared host per process (parallel graph branches must not open two sessions)."""
    global _HOST
    with _HOST_LOCK:
        if _HOST is None:
            _HOST = McpToolHost()
        return _HOST
