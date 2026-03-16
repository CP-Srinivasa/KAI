"""
MCP Adapter
============
Adapter layer for exposing the ToolRegistry as a Model Context Protocol (MCP) server.

MCP (Model Context Protocol) is an open standard by Anthropic for connecting
AI agents to external tools/data sources. This adapter bridges our internal
ToolRegistry with MCP-compatible consumers (Claude Desktop, Claude API, etc.)

Architecture:
    ToolRegistry  ──►  MCPAdapter  ──►  MCP Server (stdio / SSE / HTTP)
                                            │
                                            ▼
                                      MCP Client (Claude, etc.)

Integration options:
    1. Stdio transport  — suitable for local processes (e.g. Claude Desktop)
    2. SSE transport    — suitable for web clients (EventSource)
    3. HTTP transport   — suitable for REST-based integrations

[REQUIRES: pip install mcp]
See: https://github.com/modelcontextprotocol/python-sdk

Usage (once mcp package is installed):
    from app.agents.tool_registry import ToolRegistry
    from app.agents.mcp_adapter import MCPAdapter

    registry = ToolRegistry.default()
    adapter = MCPAdapter(registry, server_name="ai-analyst-trading-bot")
    adapter.run_stdio()       # stdio transport (e.g. Claude Desktop config)
    # or
    adapter.run_sse(port=8765)  # SSE transport

Claude Desktop config (~/.config/claude_desktop_config.json):
    {
        "mcpServers": {
            "ai-analyst": {
                "command": "python",
                "args": ["-m", "app.agents.mcp_server"],
                "cwd": "/path/to/ai_analyst_trading_bot"
            }
        }
    }
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.agents.tool_registry import ToolRegistry

logger = get_logger(__name__)

_MCP_AVAILABLE = False
try:
    import mcp  # noqa: F401
    _MCP_AVAILABLE = True
except ImportError:
    pass


class MCPAdapter:
    """
    Wraps a ToolRegistry to expose tools via the Model Context Protocol.

    When the `mcp` package is installed, this adapter can run as a full
    MCP server. Without it, it provides schema introspection and a no-op
    run method so the rest of the codebase can import cleanly.

    [REQUIRES: pip install mcp]
    """

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        server_name: str = "ai-analyst-trading-bot",
        server_version: str = "0.1.0",
    ) -> None:
        self._registry = registry or ToolRegistry.default()
        self._server_name = server_name
        self._server_version = server_version
        self._mcp_server: Any = None

        if _MCP_AVAILABLE:
            self._init_mcp_server()
        else:
            logger.warning(
                "mcp_package_not_installed",
                hint="pip install mcp",
                adapter="MCPAdapter running in stub mode",
            )

    # ── MCP Server Lifecycle ───────────────────────────────────────────────

    def _init_mcp_server(self) -> None:
        """
        Initialize the MCP Server object and register tools from the registry.
        Only called when the `mcp` package is installed.
        """
        try:
            from mcp.server import Server  # type: ignore[import]
            from mcp.server.models import InitializationOptions  # type: ignore[import]
            import mcp.types as mcp_types  # type: ignore[import]

            server = Server(self._server_name)

            # Register list_tools handler
            @server.list_tools()
            async def handle_list_tools() -> list[mcp_types.Tool]:
                return [
                    mcp_types.Tool(
                        name=t.name,
                        description=t.description,
                        inputSchema=t.parameters,
                    )
                    for t in self._registry.list_tools()
                ]

            # Register call_tool handler
            @server.call_tool()
            async def handle_call_tool(
                name: str,
                arguments: dict[str, Any],
            ) -> list[mcp_types.TextContent]:
                try:
                    result = await self._registry.call(name, arguments)
                    import json
                    return [mcp_types.TextContent(
                        type="text",
                        text=json.dumps(result, default=str, indent=2),
                    )]
                except KeyError as e:
                    return [mcp_types.TextContent(
                        type="text",
                        text=f"Error: Tool '{name}' not found. {e}",
                    )]
                except Exception as e:
                    logger.error("mcp_tool_call_error", tool=name, error=str(e))
                    return [mcp_types.TextContent(
                        type="text",
                        text=f"Error executing tool '{name}': {e}",
                    )]

            self._mcp_server = server
            logger.info(
                "mcp_server_initialized",
                server=self._server_name,
                tools=len(self._registry.list_tools()),
            )

        except Exception as e:
            logger.error("mcp_server_init_failed", error=str(e))
            self._mcp_server = None

    def run_stdio(self) -> None:
        """
        Run the MCP server over stdio transport.
        Used for local integrations (e.g. Claude Desktop).

        [REQUIRES: pip install mcp]
        """
        if not _MCP_AVAILABLE or not self._mcp_server:
            logger.error(
                "mcp_not_available",
                hint="pip install mcp",
                mode="stdio",
            )
            return

        import asyncio
        from mcp.server.stdio import stdio_server  # type: ignore[import]

        async def _run() -> None:
            async with stdio_server() as (read_stream, write_stream):
                await self._mcp_server.run(
                    read_stream,
                    write_stream,
                    self._mcp_server.create_initialization_options(),
                )

        logger.info("mcp_server_starting", transport="stdio")
        asyncio.run(_run())

    def run_sse(self, host: str = "0.0.0.0", port: int = 8765) -> None:
        """
        Run the MCP server over SSE (Server-Sent Events) transport.
        Suitable for web-based MCP clients.

        [REQUIRES: pip install mcp]
        """
        if not _MCP_AVAILABLE or not self._mcp_server:
            logger.error(
                "mcp_not_available",
                hint="pip install mcp",
                mode="sse",
            )
            return

        import asyncio
        from mcp.server.sse import SseServerTransport  # type: ignore[import]
        import uvicorn  # type: ignore[import]
        from starlette.applications import Starlette  # type: ignore[import]
        from starlette.routing import Route  # type: ignore[import]

        sse_transport = SseServerTransport("/messages")

        async def handle_sse(request: Any) -> Any:
            async with sse_transport.connect_sse(
                request.scope, request.receive, request._send
            ) as (read, write):
                await self._mcp_server.run(
                    read, write,
                    self._mcp_server.create_initialization_options(),
                )

        app = Starlette(routes=[Route("/sse", endpoint=handle_sse)])

        logger.info("mcp_server_starting", transport="sse", host=host, port=port)
        uvicorn.run(app, host=host, port=port)

    # ── Schema Inspection (no mcp package needed) ──────────────────────────

    def list_mcp_tools(self) -> list[dict[str, Any]]:
        """Return all tools in MCP Tool format (no mcp package needed)."""
        return self._registry.mcp_tools()

    def list_openai_tools(self) -> list[dict[str, Any]]:
        """Return all tools in OpenAI function-calling format."""
        return self._registry.openai_tools()

    def is_available(self) -> bool:
        """True if the mcp package is installed and the server is initialized."""
        return _MCP_AVAILABLE and self._mcp_server is not None

    def status(self) -> dict[str, Any]:
        """Return adapter status for health checks."""
        return {
            "mcp_package_installed": _MCP_AVAILABLE,
            "server_initialized": self._mcp_server is not None,
            "server_name": self._server_name,
            "server_version": self._server_version,
            "registered_tools": len(self._registry.list_tools()),
            "tool_names": [t.name for t in self._registry.list_tools()],
        }
