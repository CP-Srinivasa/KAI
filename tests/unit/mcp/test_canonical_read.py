"""Tests for the MCP canonical_read tool inventory module.

Verifies that every tool in CANONICAL_READ_TOOL_NAMES is actually registered
in the FastMCP server instance.
"""

from __future__ import annotations

import pytest

from app.agents.mcp_server import mcp
from app.agents.tools.canonical_read import CANONICAL_READ_TOOL_NAMES, get_canonical_read_tool_names


@pytest.mark.asyncio
async def test_canonical_read_tools_all_registered_in_mcp_server() -> None:
    """Every name in CANONICAL_READ_TOOL_NAMES must be registered in the MCP server."""
    registered = {tool.name for tool in await mcp.list_tools()}
    for name in CANONICAL_READ_TOOL_NAMES:
        assert name in registered, f"Canonical read tool not registered in MCP: {name}"


def test_get_canonical_read_tool_names_returns_tuple() -> None:
    names = get_canonical_read_tool_names()
    assert isinstance(names, tuple)
    assert len(names) > 0


def test_canonical_read_inventory_has_no_duplicates() -> None:
    names = get_canonical_read_tool_names()
    assert len(names) == len(set(names)), "Duplicate entries found in canonical_read inventory"


def test_canonical_read_tools_are_get_prefixed() -> None:
    """All canonical read-only tools must start with 'get_'."""
    for name in CANONICAL_READ_TOOL_NAMES:
        assert name.startswith("get_"), f"Non-get_ tool in canonical_read: {name}"


def test_canonical_read_inventory_includes_key_tools() -> None:
    """Spot-check: core read-only tools must be present."""
    required = {
        "get_watchlists",
        "get_research_brief",
        "get_signal_candidates",
        "get_paper_portfolio_snapshot",
        "get_trading_loop_status",
        "get_recent_trading_cycles",
        "get_decision_journal_summary",
    }
    assert required.issubset(set(CANONICAL_READ_TOOL_NAMES))
