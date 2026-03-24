"""Sprint 18: path guards + write audit tests (I-94, I-95)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.mcp_server import (
    activate_route_profile,
    create_inference_profile,
    deactivate_route_profile,
    get_mcp_capabilities,
)
from tests.unit.mcp._helpers import (
    _patch_workspace_root,
    _write_route_profile,
)


@pytest.mark.asyncio
async def test_create_inference_profile_rejects_non_artifacts_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-95: output path outside artifacts/ must be rejected."""
    _patch_workspace_root(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="must be within workspace/artifacts/"):
        await create_inference_profile(
            profile_name="bad-location",
            route_profile="primary_only",
            output_path="inference_route_profile.json",  # workspace root — not artifacts/
        )


@pytest.mark.asyncio
async def test_activate_route_profile_rejects_non_artifacts_state_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-95: state_path outside artifacts/ must be rejected."""
    _patch_workspace_root(monkeypatch, tmp_path)
    profile_path = _write_route_profile(tmp_path)

    with pytest.raises(ValueError, match="must be within workspace/artifacts/"):
        await activate_route_profile(
            str(profile_path),
            state_path="active_route.json",  # workspace root — not artifacts/
        )


@pytest.mark.asyncio
async def test_deactivate_route_profile_rejects_non_artifacts_state_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-95: state_path outside artifacts/ must be rejected."""
    _patch_workspace_root(monkeypatch, tmp_path)
    state_path = tmp_path / "active_route.json"  # workspace root — not artifacts/

    with pytest.raises(ValueError, match="must be within workspace/artifacts/"):
        await deactivate_route_profile(str(state_path))


@pytest.mark.asyncio
async def test_create_inference_profile_appends_write_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-94: create_inference_profile must append a JSONL audit entry."""
    _patch_workspace_root(monkeypatch, tmp_path)

    await create_inference_profile(
        profile_name="audit-test",
        route_profile="primary_with_shadow",
        shadow_paths=["B.companion"],
        output_path="artifacts/routes/audit_profile.json",
    )

    audit_path = tmp_path / "artifacts" / "mcp_write_audit.jsonl"
    assert audit_path.exists()
    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["tool"] == "create_inference_profile"
    assert entry["params"]["profile_name"] == "audit-test"
    assert "timestamp" in entry


@pytest.mark.asyncio
async def test_activate_route_profile_appends_write_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-94: activate_route_profile must append a JSONL audit entry."""
    _patch_workspace_root(monkeypatch, tmp_path)
    profile_path = _write_route_profile(tmp_path)

    await activate_route_profile(
        str(profile_path),
        state_path="artifacts/active_route.json",
    )

    audit_path = tmp_path / "artifacts" / "mcp_write_audit.jsonl"
    assert audit_path.exists()
    entry = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert entry["tool"] == "activate_route_profile"
    assert "state_path" in entry["params"]
    assert entry["params"]["abc_envelope_output"] is None


@pytest.mark.asyncio
async def test_deactivate_route_profile_appends_write_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-94: deactivate_route_profile must append audit even when nothing to remove."""
    _patch_workspace_root(monkeypatch, tmp_path)
    state_path = tmp_path / "artifacts" / "missing_active.json"

    await deactivate_route_profile(str(state_path))

    audit_path = tmp_path / "artifacts" / "mcp_write_audit.jsonl"
    assert audit_path.exists()
    entry = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert entry["tool"] == "deactivate_route_profile"
    assert "deactivated: False" in entry["result_summary"]


@pytest.mark.asyncio
async def test_mcp_write_audit_accumulates_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-94: multiple write calls produce multiple JSONL lines (append mode)."""
    _patch_workspace_root(monkeypatch, tmp_path)

    await create_inference_profile(
        profile_name="first",
        route_profile="primary_only",
        output_path="artifacts/routes/first.json",
    )
    state_path = tmp_path / "artifacts" / "missing.json"
    await deactivate_route_profile(str(state_path))

    audit_path = tmp_path / "artifacts" / "mcp_write_audit.jsonl"
    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    tools = [json.loads(line)["tool"] for line in lines]
    assert tools == ["create_inference_profile", "deactivate_route_profile"]


@pytest.mark.asyncio
async def test_get_mcp_capabilities_reports_write_guard_guardrails() -> None:
    """I-94/I-95: capabilities surface must document write guard invariants."""
    payload = json.loads(await get_mcp_capabilities())

    guardrails = payload["guardrails"]
    assert any("I-95" in g for g in guardrails)
    assert any("I-94" in g for g in guardrails)
