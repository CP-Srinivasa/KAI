"""Sprint 19: handoff acknowledgement tests (I-101–I-104, I-116, I-118)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.mcp_server import (
    acknowledge_signal_handoff,
    get_handoff_collector_summary,
    get_handoff_summary,
)
from app.research.execution_handoff import (
    HANDOFF_ACK_JSONL_FILENAME,
    append_handoff_acknowledgement_jsonl,
    create_handoff_acknowledgement,
    load_signal_handoffs,
)
from tests.unit.mcp._helpers import (
    _patch_workspace_root,
    _write_signal_handoff_batch,
)


@pytest.mark.asyncio
async def test_acknowledge_signal_handoff_writes_audit_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Signal acknowledgement writes a canonical handoff audit record (I-116)."""
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, payload = _write_signal_handoff_batch(tmp_path / "artifacts" / "handoffs.jsonl")

    result = await acknowledge_signal_handoff(
        handoff_path=str(handoff_path),
        handoff_id=str(payload["handoff_id"]),
        consumer_agent_id="test-agent-001",
    )

    assert result["status"] == "acknowledged_in_audit_only"
    assert result["handoff_id"] == payload["handoff_id"]
    assert result["signal_id"] == payload["signal_id"]
    assert result["consumer_agent_id"] == "test-agent-001"
    assert result["handoff_path"] == str(handoff_path.resolve())

    ack_path = tmp_path / "artifacts" / HANDOFF_ACK_JSONL_FILENAME
    assert ack_path.exists()
    record = json.loads(ack_path.read_text(encoding="utf-8").strip())
    assert record["handoff_id"] == payload["handoff_id"]
    assert record["signal_id"] == payload["signal_id"]
    assert record["status"] == "acknowledged"
    assert record["consumer_visibility"] == "visible"


@pytest.mark.asyncio
async def test_acknowledge_signal_handoff_appends_mcp_write_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit acknowledgements must create an MCP write audit entry (I-94)."""
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, payload = _write_signal_handoff_batch(tmp_path / "artifacts" / "handoffs.jsonl")

    await acknowledge_signal_handoff(
        handoff_path=str(handoff_path),
        handoff_id=str(payload["handoff_id"]),
        consumer_agent_id="agent-002",
    )

    mcp_audit = tmp_path / "artifacts" / "mcp_write_audit.jsonl"
    assert mcp_audit.exists()
    record = json.loads(mcp_audit.read_text(encoding="utf-8").strip())
    assert record["tool"] == "acknowledge_signal_handoff"


@pytest.mark.asyncio
async def test_get_handoff_collector_summary_returns_pending_when_no_audit_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, _payload = _write_signal_handoff_batch(tmp_path / "artifacts" / "handoffs.jsonl")

    result = await get_handoff_collector_summary(handoff_path=str(handoff_path))

    assert result["total_handoffs"] == 1
    assert result["acknowledged_count"] == 0
    assert result["pending_count"] == 1


@pytest.mark.asyncio
async def test_get_handoff_summary_reads_consumer_acknowledgements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_handoff_summary is a compatibility alias for the collector summary."""
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, payload = _write_signal_handoff_batch(tmp_path / "artifacts" / "handoffs.jsonl")
    handoff = load_signal_handoffs(handoff_path)[0]
    ack_path = tmp_path / "artifacts" / HANDOFF_ACK_JSONL_FILENAME
    ack_path.parent.mkdir(parents=True, exist_ok=True)

    append_handoff_acknowledgement_jsonl(
        create_handoff_acknowledgement(
            handoff,
            consumer_agent_id="agent-A",
        ),
        ack_path,
    )

    result = await get_handoff_summary(handoff_path=str(handoff_path))

    assert result["total_handoffs"] == 1
    assert result["acknowledged_count"] == 1
    assert result["pending_count"] == 0
    assert result["consumers"]["agent-A"] == 1
    assert result["acknowledged_handoffs"][0]["signal_id"] == payload["signal_id"]


@pytest.mark.asyncio
async def test_acknowledge_signal_handoff_rejects_hidden_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shadow/control handoffs stay audit-only and cannot be externally acknowledged."""
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, payload = _write_signal_handoff_batch(tmp_path / "artifacts" / "handoffs.jsonl")
    hidden_payload = dict(payload)
    hidden_payload["route_path"] = "B.companion"
    hidden_payload["path_type"] = "shadow"
    hidden_payload["delivery_class"] = "audit_only"
    hidden_payload["consumer_visibility"] = "hidden"
    handoff_path.write_text(json.dumps(hidden_payload) + "\n", encoding="utf-8")

    with pytest.raises(PermissionError, match="consumer-visible"):
        await acknowledge_signal_handoff(
            handoff_path=str(handoff_path),
            handoff_id=str(payload["handoff_id"]),
            consumer_agent_id="safety-check",
        )


@pytest.mark.asyncio
async def test_acknowledge_signal_handoff_no_db_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """acknowledge_signal_handoff MUST NOT touch the KAI-Core DB (I-118)."""
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, payload = _write_signal_handoff_batch(tmp_path / "artifacts" / "handoffs.jsonl")

    called = []

    def _fail_if_called(*_a: object, **_kw: object) -> None:
        called.append(True)

    monkeypatch.setattr("app.agents.tools._helpers.build_session_factory", _fail_if_called)

    await acknowledge_signal_handoff(
        handoff_path=str(handoff_path),
        handoff_id=str(payload["handoff_id"]),
        consumer_agent_id="safety-check",
    )

    assert not called, "acknowledge_signal_handoff must not call build_session_factory"
