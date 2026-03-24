"""Inference route profile read/write lifecycle tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.mcp_server import (
    activate_route_profile,
    create_inference_profile,
    deactivate_route_profile,
    get_active_route_status,
    get_inference_route_profile,
    get_upgrade_cycle_status,
)
from tests.unit.mcp._helpers import (
    _patch_workspace_root,
    _write_route_profile,
    _write_teacher_dataset,
)


@pytest.mark.asyncio
async def test_get_inference_route_profile_reads_workspace_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    profile_path = _write_route_profile(tmp_path)

    payload = await get_inference_route_profile(str(profile_path))

    assert payload["report_type"] == "inference_route_profile"
    assert payload["profile_name"] == "mcp-route"
    assert payload["path"] == str(profile_path.resolve())


@pytest.mark.asyncio
async def test_get_inference_route_profile_blocks_outside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    outside_path = tmp_path.parent / "outside_profile.json"

    with pytest.raises(ValueError, match="must stay within workspace"):
        await get_inference_route_profile(str(outside_path))


@pytest.mark.asyncio
async def test_get_active_route_status_returns_inactive_when_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)

    payload = await get_active_route_status()

    assert payload["active"] is False
    assert payload["state_path"].endswith("artifacts\\active_route_profile.json") or payload[
        "state_path"
    ].endswith("artifacts/active_route_profile.json")


@pytest.mark.asyncio
async def test_get_upgrade_cycle_status_reads_existing_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    teacher_dataset = tmp_path / "artifacts" / "teacher.jsonl"
    _write_teacher_dataset(teacher_dataset)

    payload = await get_upgrade_cycle_status(str(teacher_dataset))

    assert payload["report_type"] == "upgrade_cycle_report"
    assert payload["status"] == "prepared"
    assert payload["teacher_dataset_path"] == str(teacher_dataset.resolve())


@pytest.mark.asyncio
async def test_create_inference_profile_writes_profile_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)

    payload = await create_inference_profile(
        profile_name="mcp-created",
        route_profile="primary_with_shadow",
        shadow_paths=["B.companion"],
        output_path="artifacts/routes/created_profile.json",
        notes=["operator-managed"],
    )

    saved_path = Path(payload["output_path"])
    assert saved_path.exists()
    data = json.loads(saved_path.read_text(encoding="utf-8"))
    assert data["profile_name"] == "mcp-created"
    assert data["route_profile"] == "primary_with_shadow"
    assert payload["profile"]["notes"] == ["operator-managed"]


@pytest.mark.asyncio
async def test_activate_route_profile_writes_guarded_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    profile_path = _write_route_profile(tmp_path)

    payload = await activate_route_profile(
        str(profile_path),
        state_path="artifacts/active_route.json",
        abc_envelope_output="artifacts/abc/envelopes.jsonl",
    )

    state_path = tmp_path / "artifacts" / "active_route.json"
    assert state_path.exists()
    assert payload["state_path"] == str(state_path.resolve())
    assert payload["state"]["route_profile"] == "primary_with_shadow"
    assert payload["state"]["abc_envelope_output"] == str(
        (tmp_path / "artifacts" / "abc" / "envelopes.jsonl").resolve()
    )


@pytest.mark.asyncio
async def test_deactivate_route_profile_removes_state_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    state_path = tmp_path / "artifacts" / "active_route.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("{}", encoding="utf-8")

    payload = await deactivate_route_profile(str(state_path))

    assert payload["deactivated"] is True
    assert not state_path.exists()


@pytest.mark.asyncio
async def test_deactivate_route_profile_is_idempotent_when_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    state_path = tmp_path / "artifacts" / "missing_active_route.json"

    payload = await deactivate_route_profile(str(state_path))

    assert payload["deactivated"] is False
    assert payload["state_path"] == str(state_path.resolve())


@pytest.mark.asyncio
async def test_create_inference_profile_rejects_non_json_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="must use one of"):
        await create_inference_profile(
            profile_name="bad-output",
            route_profile="primary_only",
            output_path="artifacts/routes/not_allowed.txt",
        )


@pytest.mark.asyncio
async def test_activate_route_profile_blocks_outside_workspace_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    profile_path = _write_route_profile(tmp_path)
    outside_state = tmp_path.parent / "active_route.json"

    with pytest.raises(ValueError, match="must stay within workspace"):
        await activate_route_profile(str(profile_path), state_path=str(outside_state))


@pytest.mark.asyncio
async def test_activate_route_profile_missing_profile_raises_file_not_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)

    with pytest.raises(FileNotFoundError, match="Inference route profile not found"):
        await activate_route_profile(str(tmp_path / "missing_profile.json"))


@pytest.mark.asyncio
async def test_activate_route_profile_returns_app_llm_provider_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-97: activate_route_profile audit record MUST include app_llm_provider_unchanged."""
    _patch_workspace_root(monkeypatch, tmp_path)
    profile_path = _write_route_profile(tmp_path)

    payload = await activate_route_profile(
        str(profile_path),
        state_path="artifacts/active_route.json",
    )

    assert payload.get("app_llm_provider_unchanged") is True
