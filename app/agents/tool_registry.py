"""
Agent Tool Registry
====================
Central registry of callable tools available to AI agents.

Design goals:
  - Provider-agnostic: tools work whether invoked by a local agent,
    an OpenAI assistant, an Anthropic Claude agent, or via MCP.
  - Typed: every tool has an input schema (JSON-compatible).
  - Observable: invocations are logged.
  - Minimal: only register tools that are actually implemented.

A "tool" in this context corresponds to a MCP Tool or an OpenAI Function.

Usage:
    registry = ToolRegistry.default()
    result = await registry.call("search_signals", {"query": "Bitcoin ETF"})
    schema = registry.openai_tools()   # for OpenAI function calling
    schema = registry.mcp_tools()      # for MCP tool list
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class AgentTool:
    """
    A registered callable tool for agent use.

    name:        Unique tool identifier (snake_case)
    description: Plain English description for the LLM
    parameters:  JSON Schema for the input parameters
    handler:     Async callable that executes the tool
    """
    name: str
    description: str
    parameters: dict[str, Any]     # JSON Schema object
    handler: Callable[..., Awaitable[Any]]
    tags: list[str] = field(default_factory=list)

    def to_openai_function(self) -> dict[str, Any]:
        """Serialize as OpenAI function-calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_mcp_tool(self) -> dict[str, Any]:
        """Serialize as MCP Tool definition."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.parameters,
        }


class ToolRegistry:
    """
    Central registry and dispatcher for agent tools.

    Tools are registered with `register()` and invoked with `call()`.
    The registry is the single point of observability for all tool calls.
    """

    def __init__(self) -> None:
        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> None:
        """Register a tool. Overwrites if name already exists."""
        self._tools[tool.name] = tool
        logger.debug("tool_registered", tool=tool.name)

    def get(self, name: str) -> AgentTool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[AgentTool]:
        return list(self._tools.values())

    def openai_tools(self) -> list[dict[str, Any]]:
        """Return tool list in OpenAI function-calling format."""
        return [t.to_openai_function() for t in self._tools.values()]

    def mcp_tools(self) -> list[dict[str, Any]]:
        """Return tool list in MCP Tool format."""
        return [t.to_mcp_tool() for t in self._tools.values()]

    async def call(self, name: str, arguments: dict[str, Any]) -> Any:
        """
        Invoke a tool by name with given arguments.

        Returns:
            Tool result (any JSON-serializable type)

        Raises:
            KeyError if tool not found
            Exception propagated from handler
        """
        tool = self._tools.get(name)
        if not tool:
            raise KeyError(f"Tool '{name}' not found in registry")

        logger.info("tool_call", tool=name, args=list(arguments.keys()))
        try:
            result = await tool.handler(**arguments)
            logger.debug("tool_call_success", tool=name)
            return result
        except Exception as e:
            logger.error("tool_call_error", tool=name, error=str(e))
            raise

    @classmethod
    def default(cls) -> "ToolRegistry":
        """
        Build the default registry with all production tools registered.
        Tools with required external dependencies check availability at call time.
        """
        registry = cls()

        # ── Signal tools ─────────────────────────────────────────────────

        async def search_signals(
            query: str,
            min_confidence: float = 0.50,
            limit: int = 10,
        ) -> list[dict[str, Any]]:
            """Search recent signal candidates matching a query."""
            from app.research.router_helpers import get_sample_candidates
            from app.analysis.ranking.trading_ranker import TradingRelevanceRanker
            candidates = get_sample_candidates()
            filtered = [c for c in candidates if query.lower() in c.title.lower()]
            ranker = TradingRelevanceRanker()
            ranked = ranker.rank(filtered)
            return [c.to_dict() for c, _ in ranked[:limit]]

        registry.register(AgentTool(
            name="search_signals",
            description="Search recent signal candidates by keyword query.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "min_confidence": {"type": "number", "description": "Minimum signal confidence (0-1)"},
                    "limit": {"type": "integer", "description": "Max results"},
                },
                "required": ["query"],
            },
            handler=search_signals,
            tags=["signals", "research"],
        ))

        # ── Research tools ────────────────────────────────────────────────

        async def get_asset_research(asset: str) -> dict[str, Any]:
            """Get the research pack for a specific asset symbol."""
            from app.research.router_helpers import get_sample_candidates
            from app.research.builder import ResearchPackBuilder
            candidates = get_sample_candidates()
            builder = ResearchPackBuilder()
            pack = builder.for_asset(asset.upper(), candidates)
            return pack.to_dict()

        registry.register(AgentTool(
            name="get_asset_research",
            description="Get full research pack (signals, evidence, risks) for a specific asset like BTC or ETH.",
            parameters={
                "type": "object",
                "properties": {
                    "asset": {"type": "string", "description": "Asset symbol (e.g. BTC, ETH, NVDA)"},
                },
                "required": ["asset"],
            },
            handler=get_asset_research,
            tags=["research", "assets"],
        ))

        async def get_daily_brief() -> dict[str, Any]:
            """Get the current daily research brief."""
            from app.research.router_helpers import get_sample_candidates
            from app.research.builder import ResearchPackBuilder
            from datetime import datetime
            candidates = get_sample_candidates()
            builder = ResearchPackBuilder()
            brief = builder.daily_brief(candidates, date=datetime.utcnow().strftime("%Y-%m-%d"))
            return brief.to_dict()

        registry.register(AgentTool(
            name="get_daily_brief",
            description="Get the daily research brief summarizing all active signals, narratives, and key themes.",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=get_daily_brief,
            tags=["research", "brief"],
        ))

        # ── Watchlist tools ───────────────────────────────────────────────

        async def search_watchlist(text: str) -> list[dict[str, Any]]:
            """Search watchlist for items matching the given text."""
            from app.trading.watchlists.watchlist import WatchlistRegistry
            from pathlib import Path
            registry_wl = WatchlistRegistry.from_file(Path("monitor/watchlists.yml"))
            matches = registry_wl.find_by_text(text)
            return [
                {
                    "category": m.item.category.value,
                    "identifier": m.item.identifier,
                    "display_name": m.item.display_name,
                    "matched_alias": m.matched_alias,
                    "tags": m.item.tags,
                }
                for m in matches
            ]

        registry.register(AgentTool(
            name="search_watchlist",
            description="Find watchlist items (assets, persons, topics) mentioned in a text snippet.",
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to scan for watchlist mentions"},
                },
                "required": ["text"],
            },
            handler=search_watchlist,
            tags=["watchlist"],
        ))

        # ── Historical tools ──────────────────────────────────────────────

        async def find_historical_analogues(
            asset: str,
            event_type: str = "",
            sentiment: str = "",
        ) -> list[dict[str, Any]]:
            """Find historical event analogues for a given asset."""
            from app.analysis.historical.matcher import HistoricalMatcher
            matcher = HistoricalMatcher()
            analogues = matcher.find(
                assets=[asset],
                event_type=event_type or None,
                sentiment=sentiment or None,
                max_results=3,
            )
            return [a.to_dict() for a in analogues]

        registry.register(AgentTool(
            name="find_historical_analogues",
            description="Find historical market event analogues for an asset. Returns past events with outcome summaries.",
            parameters={
                "type": "object",
                "properties": {
                    "asset": {"type": "string", "description": "Asset symbol (e.g. BTC)"},
                    "event_type": {"type": "string", "description": "Optional event type filter"},
                    "sentiment": {"type": "string", "description": "Optional sentiment filter: positive | negative | neutral"},
                },
                "required": ["asset"],
            },
            handler=find_historical_analogues,
            tags=["historical", "research"],
        ))

        return registry
