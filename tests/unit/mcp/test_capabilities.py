"""MCP capabilities + inventory + sprint-32 coverage tests."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.mcp_server import (
    get_mcp_capabilities,
    get_mcp_tool_inventory,
    get_narrative_clusters,
    get_operational_escalation_summary,
    mcp,
)


@pytest.mark.asyncio
async def test_get_mcp_capabilities_reports_guardrails() -> None:
    payload = json.loads(await get_mcp_capabilities())

    assert payload["transport"] == "stdio_only"
    assert "get_signals_for_execution" in payload["read_tools"]
    assert "get_distribution_classification_report" in payload["read_tools"]
    assert "get_upgrade_cycle_status" in payload["read_tools"]
    assert "get_operational_readiness_summary" in payload["read_tools"]
    assert "get_protective_gate_summary" in payload["read_tools"]
    assert "get_remediation_recommendations" in payload["read_tools"]
    assert "get_escalation_summary" in payload["read_tools"]
    assert "get_blocking_summary" in payload["read_tools"]
    assert "get_operator_action_summary" in payload["read_tools"]
    assert "get_action_queue_summary" in payload["read_tools"]
    assert "get_blocking_actions" in payload["read_tools"]
    assert "get_prioritized_actions" in payload["read_tools"]
    assert "get_review_required_actions" in payload["read_tools"]
    assert "get_review_journal_summary" in payload["read_tools"]
    assert "get_resolution_summary" in payload["read_tools"]
    assert "get_daily_operator_summary" in payload["read_tools"]
    assert "get_artifact_inventory" in payload["read_tools"]
    assert "get_artifact_retention_report" in payload["read_tools"]
    assert "get_cleanup_eligibility_summary" in payload["read_tools"]
    assert "get_protected_artifact_summary" in payload["read_tools"]
    assert "get_review_required_summary" in payload["read_tools"]
    assert "get_policy_rationale_summary" not in payload["read_tools"]
    assert "get_governance_summary" not in payload["read_tools"]
    assert "activate_route_profile" in payload["write_tools"]
    assert payload["write_tools"] == payload["guarded_write_tools"]
    assert "acknowledge_signal_handoff" in payload["write_tools"]
    assert "append_review_journal_entry" in payload["write_tools"]
    assert (
        payload["aliases"]["get_handoff_summary"]["canonical_tool"]
        == "get_handoff_collector_summary"
    )
    assert (
        payload["aliases"]["get_operator_decision_pack"]["canonical_tool"]
        == "get_decision_pack_summary"
    )
    assert (
        payload["superseded_tools"]["get_operational_escalation_summary"]["replacement_tool"]
        == "get_escalation_summary"
    )
    assert "No direct execution hook for signals" in payload["guardrails"]
    assert "audit-only" in " ".join(payload["guardrails"])
    assert "no auto-deletion" in " ".join(payload["guardrails"]).lower()
    assert "No auto-routing or auto-promotion" in payload["guardrails"]


@pytest.mark.asyncio
async def test_get_mcp_tool_inventory_classifies_canonical_alias_and_superseded_tools() -> None:
    inventory = get_mcp_tool_inventory()

    assert "get_narrative_clusters" in inventory["canonical_read_tools"]
    assert "get_review_journal_summary" in inventory["canonical_read_tools"]
    assert "get_resolution_summary" in inventory["canonical_read_tools"]
    assert "get_daily_operator_summary" in inventory["canonical_read_tools"]
    assert "get_handoff_summary" not in inventory["canonical_read_tools"]
    assert "get_operator_decision_pack" not in inventory["canonical_read_tools"]
    assert "append_review_journal_entry" in inventory["guarded_write_tools"]
    assert (
        inventory["aliases"]["get_handoff_summary"]["canonical_tool"]
        == "get_handoff_collector_summary"
    )
    assert inventory["aliases"]["get_handoff_summary"]["tool_class"] == "read_only"
    assert (
        inventory["aliases"]["get_operator_decision_pack"]["canonical_tool"]
        == "get_decision_pack_summary"
    )
    assert (
        inventory["superseded_tools"]["get_operational_escalation_summary"]["replacement_tool"]
        == "get_escalation_summary"
    )
    assert (
        inventory["superseded_tools"]["get_operational_escalation_summary"]["tool_class"]
        == "read_only"
    )
    assert set(inventory["canonical_read_tools"]).isdisjoint(inventory["guarded_write_tools"])


@pytest.mark.asyncio
async def test_mcp_tool_inventory_matches_registered_tools() -> None:
    inventory = get_mcp_tool_inventory()
    tools = await mcp.list_tools()
    registered = {tool.name for tool in tools}
    classified = (
        set(inventory["canonical_read_tools"])
        | set(inventory["guarded_write_tools"])
        | set(inventory["workflow_helpers"])
        | set(inventory["aliases"])
        | set(inventory["superseded_tools"])
    )

    assert registered == classified


# ---------------------------------------------------------------------------
# Sprint 32: Coverage Completion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.agents.tools.canonical_read.build_session_factory")
@patch("app.agents.tools.canonical_read.get_settings")
async def test_get_narrative_clusters_returns_read_only_report(
    mock_settings: MagicMock,
    mock_session_factory: MagicMock,
) -> None:
    """get_narrative_clusters stays read-only and execution-disabled."""
    mock_settings.return_value = SimpleNamespace(db=MagicMock())

    mock_session = AsyncMock()
    mock_session_factory.return_value.begin.return_value.__aenter__.return_value = mock_session

    with patch("app.agents.tools.canonical_read.DocumentRepository") as mock_repo_cls:
        mock_repo = mock_repo_cls.return_value
        mock_repo.list = AsyncMock(return_value=[])

        result = await get_narrative_clusters(min_priority=8, limit=10)

    assert result["report_type"] == "narrative_cluster_report"
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False
    assert result["cluster_count"] == 0
    assert result["candidate_count"] == 0
    assert isinstance(result["clusters"], list)


@pytest.mark.asyncio
async def test_get_operational_escalation_summary_excluded_from_read_tools() -> None:
    """Superseded escalation alias stays out of read_tools."""
    caps = json.loads(await get_mcp_capabilities())
    assert "get_operational_escalation_summary" not in caps["read_tools"], (
        "Superseded tool must not appear in read_tools (I-204, I-212)"
    )
    assert "get_escalation_summary" in caps["read_tools"], (
        "Canonical escalation tool must remain in read_tools"
    )


@pytest.mark.asyncio
async def test_get_operational_escalation_summary_returns_valid_structure(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Superseded escalation alias still returns a read-only payload."""
    from tests.unit.mcp._helpers import _patch_workspace_root

    _patch_workspace_root(monkeypatch, tmp_path)

    result = await get_operational_escalation_summary()

    assert result["report_type"] == "operational_escalation_summary"
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False


@pytest.mark.asyncio
async def test_get_mcp_capabilities_sprint32_contract() -> None:
    """Sprint 32 keeps canonical reads and excludes superseded reads."""
    caps = json.loads(await get_mcp_capabilities())

    assert "get_narrative_clusters" in caps["read_tools"], (
        "get_narrative_clusters must be in read_tools (I-205, I-214)"
    )
    assert "get_operational_escalation_summary" not in caps["read_tools"], (
        "Superseded get_operational_escalation_summary must not be in read_tools (I-204, I-215)"
    )
