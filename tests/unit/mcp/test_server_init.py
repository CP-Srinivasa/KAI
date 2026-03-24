"""MCP server initialisation + tool registration tests."""
from __future__ import annotations

import pytest
from mcp.server.fastmcp import FastMCP

from app.agents.mcp_server import mcp


def test_mcp_server_initialization() -> None:
    assert isinstance(mcp, FastMCP)
    assert mcp.name == "KAI Analyst Trading Bot"


@pytest.mark.asyncio
async def test_mcp_server_tools_registered() -> None:
    tools = await mcp.list_tools()
    tool_names = {tool.name for tool in tools}

    assert {
        "get_watchlists",
        "get_research_brief",
        "get_signal_candidates",
        "get_signals_for_execution",
        "get_distribution_classification_report",
        "get_route_profile_report",
        "get_inference_route_profile",
        "get_active_route_status",
        "get_upgrade_cycle_status",
        "get_handoff_collector_summary",
        "get_operational_readiness_summary",
        "get_protective_gate_summary",
        "get_remediation_recommendations",
        "append_review_journal_entry",
        "get_review_journal_summary",
        "get_resolution_summary",
        "acknowledge_signal_handoff",
        "create_inference_profile",
        "activate_route_profile",
        "deactivate_route_profile",
        "get_mcp_capabilities",
    }.issubset(tool_names)
