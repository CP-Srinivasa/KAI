"""Action queue tests: blocking/operator/decision-pack/runbook surfaces."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.mcp_server import (
    activate_route_profile,
    get_action_queue_summary,
    get_blocking_actions,
    get_blocking_summary,
    get_decision_pack_summary,
    get_operator_action_summary,
    get_operator_decision_pack,
    get_operator_runbook,
    get_prioritized_actions,
    get_review_required_actions,
)
from tests.unit.mcp._helpers import (
    _patch_workspace_root,
    _write_route_profile,
    _write_signal_handoff_batch,
)


@pytest.mark.asyncio
async def test_get_blocking_summary_filters_blocking_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, _payload = _write_signal_handoff_batch(tmp_path / "artifacts" / "handoffs.jsonl")
    profile_path = _write_route_profile(
        tmp_path,
        route_profile="primary_with_shadow_and_control",
        shadow_paths=["B.companion"],
        control_path="C.rule",
    )
    await activate_route_profile(
        str(profile_path),
        state_path="artifacts/active_route_profile.json",
        abc_envelope_output="artifacts/abc/missing_envelopes.jsonl",
    )

    result = await get_blocking_summary(
        handoff_path=str(handoff_path),
        state_path="artifacts/active_route_profile.json",
    )

    assert result["report_type"] == "blocking_summary"
    assert result["blocking"] is True
    assert result["blocking_count"] >= 1
    assert result["severity"] == "critical"
    assert result["items"]
    assert all(item["blocking"] is True for item in result["items"])


@pytest.mark.asyncio
async def test_get_operator_action_summary_includes_review_required_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, _payload = _write_signal_handoff_batch(tmp_path / "artifacts" / "handoffs.jsonl")
    (tmp_path / "artifacts" / "manual_review_blob.json").write_text(
        "{}",
        encoding="utf-8",
    )

    result = await get_operator_action_summary(
        handoff_path=str(handoff_path),
        artifacts_dir="artifacts",
    )

    assert result["report_type"] == "operator_action_summary"
    assert result["blocking"] is False
    assert result["operator_action_count"] >= 1
    assert result["review_required_count"] == 1
    assert any(item["category"] == "review_required" for item in result["items"])


@pytest.mark.asyncio
async def test_get_action_queue_summary_returns_prioritized_read_only_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, _payload = _write_signal_handoff_batch(tmp_path / "artifacts" / "handoffs.jsonl")
    profile_path = _write_route_profile(
        tmp_path,
        route_profile="primary_with_shadow_and_control",
        shadow_paths=["B.companion"],
        control_path="C.rule",
    )
    await activate_route_profile(
        str(profile_path),
        state_path="artifacts/active_route_profile.json",
        abc_envelope_output="artifacts/abc/missing_envelopes.jsonl",
    )
    (tmp_path / "artifacts" / "manual_review_blob.json").write_text(
        "{}",
        encoding="utf-8",
    )

    result = await get_action_queue_summary(
        handoff_path=str(handoff_path),
        state_path="artifacts/active_route_profile.json",
        artifacts_dir="artifacts",
    )

    assert result["report_type"] == "action_queue_summary"
    assert result["queue_status"] == "blocking"
    assert result["blocking_count"] >= 1
    assert result["review_required_count"] == 1
    assert result["highest_priority"] == "p1"
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False
    assert result["items"]
    assert result["items"][0]["priority"] == "p1"
    assert result["items"][0]["action_id"].startswith("act_")


@pytest.mark.asyncio
async def test_get_blocking_actions_filters_only_blocking_queue_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, _payload = _write_signal_handoff_batch(tmp_path / "artifacts" / "handoffs.jsonl")
    profile_path = _write_route_profile(
        tmp_path,
        route_profile="primary_with_shadow_and_control",
        shadow_paths=["B.companion"],
        control_path="C.rule",
    )
    await activate_route_profile(
        str(profile_path),
        state_path="artifacts/active_route_profile.json",
        abc_envelope_output="artifacts/abc/missing_envelopes.jsonl",
    )

    result = await get_blocking_actions(
        handoff_path=str(handoff_path),
        state_path="artifacts/active_route_profile.json",
    )

    assert result["report_type"] == "blocking_actions_summary"
    assert result["queue_status"] == "blocking"
    assert result["blocking_count"] >= 1
    assert result["highest_priority"] == "p1"
    assert result["items"]
    assert all(item["queue_status"] == "blocking" for item in result["items"])


@pytest.mark.asyncio
async def test_get_prioritized_actions_returns_priority_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, _payload = _write_signal_handoff_batch(tmp_path / "artifacts" / "handoffs.jsonl")
    (tmp_path / "artifacts" / "manual_review_blob.json").write_text(
        "{}",
        encoding="utf-8",
    )

    result = await get_prioritized_actions(
        handoff_path=str(handoff_path),
        artifacts_dir="artifacts",
    )

    assert result["report_type"] == "prioritized_actions_summary"
    assert result["action_count"] >= 1
    assert result["highest_priority"] == "p2"
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False
    priorities = [item["priority"] for item in result["items"]]
    assert priorities == sorted(priorities)


@pytest.mark.asyncio
async def test_get_review_required_actions_filters_review_required_queue_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, _payload = _write_signal_handoff_batch(tmp_path / "artifacts" / "handoffs.jsonl")
    (tmp_path / "artifacts" / "manual_review_blob.json").write_text(
        "{}",
        encoding="utf-8",
    )

    result = await get_review_required_actions(
        handoff_path=str(handoff_path),
        artifacts_dir="artifacts",
    )

    assert result["report_type"] == "review_required_actions_summary"
    assert result["queue_status"] == "review_required"
    assert result["review_required_count"] == 1
    assert result["highest_priority"] == "p2"
    assert len(result["items"]) == 1
    assert result["items"][0]["queue_status"] == "review_required"
    assert result["items"][0]["blocking"] is False


@pytest.mark.asyncio
async def test_get_decision_pack_summary_returns_canonical_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, _payload = _write_signal_handoff_batch(tmp_path / "artifacts" / "handoffs.jsonl")
    profile_path = _write_route_profile(
        tmp_path / "artifacts",
        route_profile="primary_with_shadow_and_control",
        shadow_paths=["B.companion"],
        control_path="C.rule",
    )
    from app.research.active_route import activate_route_profile

    activate_route_profile(
        profile_path=profile_path,
        state_path=tmp_path / "artifacts" / "active_route_profile.json",
        abc_envelope_output=tmp_path / "artifacts" / "routes" / "missing_abc.jsonl",
    )
    (tmp_path / "artifacts" / "manual_review_blob.json").write_text(
        "{}",
        encoding="utf-8",
    )

    result = await get_decision_pack_summary(
        handoff_path=str(handoff_path),
        artifacts_dir="artifacts",
    )

    assert result["report_type"] == "operator_decision_pack"
    assert result["overall_status"] == "blocking"
    assert result["blocking_count"] >= 1
    assert result["review_required_count"] == 1
    assert result["action_queue_count"] >= 2
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False
    assert result["readiness_summary"]["report_type"] == "operational_readiness"
    assert result["blocking_summary"]["report_type"] == "blocking_summary"
    assert result["action_queue_summary"]["report_type"] == "action_queue_summary"
    assert result["review_required_summary"]["report_type"] == "review_required_artifact_summary"


@pytest.mark.asyncio
async def test_get_operator_decision_pack_alias_matches_canonical_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, _payload = _write_signal_handoff_batch(tmp_path / "artifacts" / "handoffs.jsonl")
    (tmp_path / "artifacts" / "manual_review_blob.json").write_text(
        "{}",
        encoding="utf-8",
    )

    canonical = await get_decision_pack_summary(
        handoff_path=str(handoff_path),
        artifacts_dir="artifacts",
    )
    alias = await get_operator_decision_pack(
        handoff_path=str(handoff_path),
        artifacts_dir="artifacts",
    )

    assert alias["report_type"] == "operator_decision_pack"
    assert alias["overall_status"] == canonical["overall_status"]
    assert alias["blocking_count"] == canonical["blocking_count"]
    assert alias["review_required_count"] == canonical["review_required_count"]
    assert alias["action_queue_count"] == canonical["action_queue_count"]


@pytest.mark.asyncio
async def test_get_operator_runbook_returns_validated_read_only_steps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.cli.main import get_registered_research_command_names
    from app.research.active_route import activate_route_profile

    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, _payload = _write_signal_handoff_batch(tmp_path / "artifacts" / "handoffs.jsonl")
    profile_path = _write_route_profile(
        tmp_path / "artifacts",
        route_profile="primary_with_shadow_and_control",
        shadow_paths=["B.companion"],
        control_path="C.rule",
    )

    activate_route_profile(
        profile_path=profile_path,
        state_path=tmp_path / "artifacts" / "active_route_profile.json",
        abc_envelope_output=tmp_path / "artifacts" / "routes" / "missing_abc.jsonl",
    )
    (tmp_path / "artifacts" / "manual_review_blob.json").write_text(
        "{}",
        encoding="utf-8",
    )

    result = await get_operator_runbook(
        handoff_path=str(handoff_path),
        state_path="artifacts/active_route_profile.json",
        artifacts_dir="artifacts",
    )

    registered = get_registered_research_command_names()
    assert result["report_type"] == "operator_runbook_summary"
    assert result["overall_status"] == "blocking"
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False
    assert result["steps"]
    assert result["next_steps"]
    assert result["next_steps"] == result["steps"][: len(result["next_steps"])]
    assert "research governance-summary" not in result["command_refs"]
    assert "research operator-runbook" not in result["command_refs"]

    for ref in result["command_refs"]:
        parts = ref.split()
        assert len(parts) == 2
        assert parts[0] == "research"
        assert parts[1] in registered


@pytest.mark.asyncio
async def test_get_operator_runbook_fails_closed_on_invalid_command_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.cli import research as cli_research

    _patch_workspace_root(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cli_research,
        "extract_runbook_command_refs",
        lambda _payload: ["research governance-summary"],
    )
    monkeypatch.setattr(
        cli_research,
        "get_invalid_research_command_refs",
        lambda refs: list(refs),
    )

    with pytest.raises(ValueError, match="invalid research command references"):
        await get_operator_runbook(artifacts_dir="artifacts")


@pytest.mark.asyncio
async def test_mcp_and_cli_command_inventory_stay_consistent_for_locked_surfaces() -> None:
    import json

    from app.agents.mcp_server import get_mcp_capabilities
    from app.cli.main import get_research_command_inventory

    payload = json.loads(await get_mcp_capabilities())
    inventory = get_research_command_inventory()

    assert "get_handoff_collector_summary" in payload["read_tools"]
    assert "get_decision_pack_summary" in payload["read_tools"]
    assert "get_daily_operator_summary" in payload["read_tools"]
    assert "get_operator_runbook" in payload["read_tools"]
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

    assert inventory["aliases"]["handoff-summary"] == "handoff-collector-summary"
    assert inventory["aliases"]["consumer-ack"] == "handoff-acknowledge"
    assert inventory["aliases"]["operator-decision-pack"] == "decision-pack-summary"
    assert "governance-summary" in inventory["superseded_commands"]
