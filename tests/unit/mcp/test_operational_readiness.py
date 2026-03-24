"""Operational readiness + provider health + drift + protective gate + escalation tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.mcp_server import (
    activate_route_profile,
    get_distribution_drift,
    get_escalation_summary,
    get_operational_escalation_summary,
    get_operational_readiness_summary,
    get_protective_gate_summary,
    get_provider_health,
    get_remediation_recommendations,
)
from app.research.execution_handoff import HANDOFF_ACK_JSONL_FILENAME
from tests.unit.mcp._helpers import (
    _patch_workspace_root,
    _write_abc_output,
    _write_route_profile,
    _write_signal_handoff_batch,
)


@pytest.mark.asyncio
async def test_get_operational_readiness_summary_reports_canonical_backlog_and_route_issues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, payload = _write_signal_handoff_batch(tmp_path / "artifacts" / "handoffs.jsonl")
    profile_path = _write_route_profile(
        tmp_path / "artifacts",
        route_profile="primary_with_shadow_and_control",
        shadow_paths=["B.companion"],
        control_path="C.rule",
    )
    missing_abc = tmp_path / "artifacts" / "routes" / "missing_abc.jsonl"

    await activate_route_profile(
        profile_path=str(profile_path),
        state_path="artifacts/active_route_profile.json",
        abc_envelope_output=str(missing_abc),
    )

    result = await get_operational_readiness_summary(
        handoff_path=str(handoff_path),
        acknowledgement_path=f"artifacts/{HANDOFF_ACK_JSONL_FILENAME}",
    )

    assert result["report_type"] == "operational_readiness"
    assert result["readiness_status"] == "critical"
    assert result["highest_severity"] == "critical"
    assert result["collector_summary"]["pending_count"] == 1
    assert result["route_summary"]["active"] is True
    assert result["route_summary"]["abc_output_available"] is False
    assert result["provider_health_summary"]["degraded_count"] == 0
    assert result["provider_health_summary"]["unavailable_count"] >= 2
    assert result["distribution_drift_summary"]["status"] == "warning"
    assert result["protective_gate_summary"]["gate_status"] == "blocking"
    assert result["protective_gate_summary"]["blocking_count"] >= 1
    categories = {issue["category"] for issue in result["issues"]}
    assert "handoff_backlog" in categories
    assert "artifact_state" in categories
    assert "provider_health" in categories
    assert "distribution_drift" in categories
    assert payload["signal_id"] == result["collector_summary"]["pending_handoffs"][0]["signal_id"]


@pytest.mark.asyncio
async def test_get_operational_readiness_summary_detects_distribution_drift_in_handoffs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, payload = _write_signal_handoff_batch(tmp_path / "artifacts" / "handoffs.jsonl")
    tampered_payload = dict(payload)
    tampered_payload["path_type"] = "shadow"
    tampered_payload["delivery_class"] = "audit_only"
    tampered_payload["consumer_visibility"] = "hidden"
    handoff_path.write_text(json.dumps(tampered_payload) + "\n", encoding="utf-8")

    result = await get_operational_readiness_summary(
        handoff_path=str(handoff_path),
        acknowledgement_path=f"artifacts/{HANDOFF_ACK_JSONL_FILENAME}",
    )

    assert result["distribution_drift_summary"]["status"] == "critical"
    assert result["distribution_drift_summary"]["classification_mismatch_count"] == 1
    categories = {issue["category"] for issue in result["issues"]}
    assert "distribution_drift" in categories


@pytest.mark.asyncio
async def test_get_provider_health_returns_readiness_derived_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, payload = _write_signal_handoff_batch(tmp_path / "artifacts" / "handoffs.jsonl")
    profile_path = _write_route_profile(
        tmp_path,
        route_profile="primary_with_shadow_and_control",
        shadow_paths=["B.companion"],
        control_path="C.rule",
    )
    abc_path = _write_abc_output(
        tmp_path / "artifacts" / "abc" / "envelopes.jsonl",
        document_id=str(payload["document_id"]),
    )
    await activate_route_profile(
        str(profile_path),
        state_path="artifacts/active_route_profile.json",
        abc_envelope_output=str(abc_path),
    )

    result = await get_provider_health(
        handoff_path=str(handoff_path),
        state_path="artifacts/active_route_profile.json",
    )

    assert result["report_type"] == "provider_health_summary"
    assert result["derived_from"] == "operational_readiness"
    assert result["healthy_count"] == 3
    assert result["degraded_count"] == 0
    assert result["unavailable_count"] == 0
    assert result["issues"] == []
    by_path = {entry["path_id"]: entry["status"] for entry in result["entries"]}
    assert by_path == {
        "A.external_llm": "healthy",
        "B.companion": "healthy",
        "C.rule": "healthy",
    }


@pytest.mark.asyncio
async def test_get_provider_health_flags_unavailable_expected_paths(
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

    result = await get_provider_health(
        handoff_path=str(handoff_path),
        state_path="artifacts/active_route_profile.json",
    )

    assert result["healthy_count"] == 1
    assert result["degraded_count"] == 0
    assert result["unavailable_count"] == 2
    assert any(issue["category"] == "provider_health" for issue in result["issues"])


@pytest.mark.asyncio
async def test_get_provider_health_blocks_path_outside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    outside = tmp_path.parent / "evil_handoffs.jsonl"

    with pytest.raises(ValueError, match="must stay within workspace"):
        await get_provider_health(handoff_path=str(outside))


@pytest.mark.asyncio
async def test_get_distribution_drift_returns_readiness_derived_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, payload = _write_signal_handoff_batch(tmp_path / "artifacts" / "handoffs.jsonl")
    profile_path = _write_route_profile(
        tmp_path,
        route_profile="primary_with_shadow_and_control",
        shadow_paths=["B.companion"],
        control_path="C.rule",
    )
    abc_path = _write_abc_output(
        tmp_path / "artifacts" / "abc" / "envelopes.jsonl",
        document_id=str(payload["document_id"]),
    )
    await activate_route_profile(
        str(profile_path),
        state_path="artifacts/active_route_profile.json",
        abc_envelope_output=str(abc_path),
    )

    result = await get_distribution_drift(
        handoff_path=str(handoff_path),
        state_path="artifacts/active_route_profile.json",
    )

    assert result["report_type"] == "distribution_drift_summary"
    assert result["derived_from"] == "operational_readiness"
    assert result["status"] == "nominal"
    assert result["production_handoff_count"] == 1
    assert result["shadow_audit_result_count"] == 1
    assert result["control_comparison_result_count"] == 1
    assert result["issues"] == []


@pytest.mark.asyncio
async def test_get_distribution_drift_detects_classification_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, _payload = _write_signal_handoff_batch(tmp_path / "artifacts" / "handoffs.jsonl")
    rows = [
        json.loads(line)
        for line in handoff_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rows[0]["path_type"] = "shadow"
    rows[0]["delivery_class"] = "audit_only"
    rows[0]["consumer_visibility"] = "hidden"
    handoff_path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    result = await get_distribution_drift(handoff_path=str(handoff_path))

    assert result["status"] == "critical"
    assert result["classification_mismatch_count"] == 1
    assert result["visibility_mismatch_count"] == 1
    assert any(issue["category"] == "distribution_drift" for issue in result["issues"])


@pytest.mark.asyncio
async def test_get_distribution_drift_blocks_path_outside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    outside = tmp_path.parent / "evil_handoffs.jsonl"

    with pytest.raises(ValueError, match="must stay within workspace"):
        await get_distribution_drift(handoff_path=str(outside))


@pytest.mark.asyncio
async def test_get_protective_gate_summary_returns_readiness_derived_gate_view(
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

    result = await get_protective_gate_summary(
        handoff_path=str(handoff_path),
        state_path="artifacts/active_route_profile.json",
    )

    assert result["report_type"] == "protective_gate_summary"
    assert result["derived_from"] == "operational_readiness"
    assert result["gate_status"] == "blocking"
    assert result["blocking_count"] >= 1
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False
    assert any(item["subsystem"] == "providers" for item in result["items"])


@pytest.mark.asyncio
async def test_get_protective_gate_summary_blocks_path_outside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    outside = tmp_path.parent / "evil_handoffs.jsonl"

    with pytest.raises(ValueError, match="must stay within workspace"):
        await get_protective_gate_summary(handoff_path=str(outside))


@pytest.mark.asyncio
async def test_get_remediation_recommendations_returns_read_only_recommendations(
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

    result = await get_remediation_recommendations(
        handoff_path=str(handoff_path),
        state_path="artifacts/active_route_profile.json",
    )

    assert result["report_type"] == "remediation_recommendation_report"
    assert result["derived_from"] == "protective_gate_summary"
    assert result["gate_status"] == "blocking"
    assert result["recommendation_count"] >= 1
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False
    recommendation = result["recommendations"][0]
    assert isinstance(recommendation["recommended_actions"], list)
    assert recommendation["recommended_actions"]
    assert isinstance(recommendation["evidence_refs"], list)


@pytest.mark.asyncio
async def test_get_escalation_summary_returns_read_only_review_aware_surface(
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

    result = await get_escalation_summary(
        handoff_path=str(handoff_path),
        state_path="artifacts/active_route_profile.json",
        artifacts_dir="artifacts",
    )

    assert result["report_type"] == "operational_escalation_summary"
    assert result["escalation_status"] == "blocking"
    assert result["blocking"] is True
    assert result["blocking_count"] >= 1
    assert result["review_required_count"] == 1
    assert result["operator_action_count"] >= 2
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False
    assert any(item["category"] == "review_required" for item in result["items"])


@pytest.mark.asyncio
async def test_get_operational_escalation_summary_alias_matches_canonical_summary(
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

    canonical = await get_escalation_summary(
        handoff_path=str(handoff_path),
        state_path="artifacts/active_route_profile.json",
        artifacts_dir="artifacts",
    )
    alias = await get_operational_escalation_summary(
        handoff_path=str(handoff_path),
        state_path="artifacts/active_route_profile.json",
    )

    assert alias["report_type"] == canonical["report_type"]
    assert alias["escalation_status"] == canonical["escalation_status"]
    assert alias["blocking_count"] == canonical["blocking_count"]
    assert alias["review_required_count"] == canonical["review_required_count"]
    assert alias["operator_action_count"] == canonical["operator_action_count"]
    assert alias["items"] == canonical["items"]


@pytest.mark.asyncio
async def test_get_escalation_summary_blocks_path_outside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    outside = tmp_path.parent / "evil_handoffs.jsonl"

    with pytest.raises(ValueError, match="must stay within workspace"):
        await get_escalation_summary(handoff_path=str(outside))
