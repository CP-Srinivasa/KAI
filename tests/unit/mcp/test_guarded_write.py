"""Tests for the MCP guarded_write tool inventory module.

Verifies that every tool in GUARDED_WRITE_TOOL_NAMES is actually registered
in the FastMCP server instance.
"""

from __future__ import annotations

import pytest

from app.agents.mcp_server import mcp
from app.agents.tools.guarded_write import GUARDED_WRITE_TOOL_NAMES, get_guarded_write_tool_names


@pytest.mark.asyncio
async def test_guarded_write_tools_all_registered_in_mcp_server() -> None:
    """Every name in GUARDED_WRITE_TOOL_NAMES must be registered in the MCP server."""
    registered = {tool.name for tool in await mcp.list_tools()}
    for name in GUARDED_WRITE_TOOL_NAMES:
        assert name in registered, f"Guarded write tool not registered in MCP: {name}"


def test_get_guarded_write_tool_names_returns_tuple() -> None:
    names = get_guarded_write_tool_names()
    assert isinstance(names, tuple)
    assert len(names) > 0


def test_guarded_write_inventory_has_no_duplicates() -> None:
    names = get_guarded_write_tool_names()
    assert len(names) == len(set(names)), "Duplicate entries found in guarded_write inventory"


def test_guarded_write_inventory_includes_key_tools() -> None:
    """Spot-check: core guarded-write tools must be present."""
    required = {
        "run_trading_loop_once",
        "append_decision_instance",
        "append_review_journal_entry",
        "acknowledge_signal_handoff",
    }
    assert required.issubset(set(GUARDED_WRITE_TOOL_NAMES))


def test_guarded_write_tools_are_disjoint_from_canonical_read() -> None:
    """No tool name should appear in both canonical_read and guarded_write."""
    from app.agents.tools.canonical_read import CANONICAL_READ_TOOL_NAMES

    overlap = set(GUARDED_WRITE_TOOL_NAMES) & set(CANONICAL_READ_TOOL_NAMES)
    assert not overlap, f"Tools appear in both inventories: {overlap}"


def test_run_trading_loop_once_is_guarded_write() -> None:
    """run_trading_loop_once must be classified as guarded write, not read."""
    assert "run_trading_loop_once" in GUARDED_WRITE_TOOL_NAMES


def test_create_inference_profile_is_guarded_write() -> None:
    assert "create_inference_profile" in GUARDED_WRITE_TOOL_NAMES
