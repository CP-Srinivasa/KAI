"""Sprint 33: append-only review journal + resolution tracking tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.mcp_server import (
    append_review_journal_entry,
    get_resolution_summary,
    get_review_journal_summary,
)
from tests.unit.mcp._helpers import _patch_workspace_root


@pytest.mark.asyncio
async def test_append_review_journal_entry_appends_audit_only_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.research.operational_readiness import load_review_journal_entries

    _patch_workspace_root(monkeypatch, tmp_path)

    result = await append_review_journal_entry(
        source_ref="rbk_123",
        operator_id="ops-1",
        review_action="note",
        review_note="Operator reviewed the blocking step.",
        evidence_refs=["artifacts/decision_pack.json"],
    )

    journal_path = tmp_path / "artifacts" / "operator_review_journal.jsonl"
    entries = load_review_journal_entries(journal_path)

    assert result["status"] == "review_journal_appended"
    assert result["core_state_unchanged"] is True
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False
    assert len(entries) == 1
    assert entries[0].source_ref == "rbk_123"
    assert entries[0].journal_status == "open"


@pytest.mark.asyncio
async def test_append_review_journal_entry_blocks_path_outside_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    outside = str(tmp_path / "review_journal.jsonl")

    with pytest.raises(ValueError, match="must be within workspace/artifacts/"):
        await append_review_journal_entry(
            source_ref="rbk_123",
            operator_id="ops-1",
            review_action="note",
            review_note="Should fail closed.",
            journal_output_path=outside,
        )


@pytest.mark.asyncio
async def test_get_review_journal_summary_returns_read_only_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    await append_review_journal_entry(
        source_ref="rbk_123",
        operator_id="ops-1",
        review_action="note",
        review_note="Still open.",
    )
    await append_review_journal_entry(
        source_ref="act_456",
        operator_id="ops-2",
        review_action="resolve",
        review_note="Resolved after operator review.",
    )

    result = await get_review_journal_summary()

    assert result["report_type"] == "review_journal_summary"
    assert result["journal_status"] == "open"
    assert result["total_count"] == 2
    assert result["open_count"] == 1
    assert result["resolved_count"] == 1
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False


@pytest.mark.asyncio
async def test_get_resolution_summary_returns_latest_source_resolution_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    await append_review_journal_entry(
        source_ref="rbk_123",
        operator_id="ops-1",
        review_action="note",
        review_note="Initial note.",
    )
    await append_review_journal_entry(
        source_ref="rbk_123",
        operator_id="ops-1",
        review_action="resolve",
        review_note="Now resolved.",
    )
    await append_review_journal_entry(
        source_ref="act_456",
        operator_id="ops-2",
        review_action="defer",
        review_note="Still open.",
    )

    result = await get_resolution_summary()

    assert result["report_type"] == "review_resolution_summary"
    assert result["journal_status"] == "open"
    assert result["open_count"] == 1
    assert result["resolved_count"] == 1
    assert result["open_source_refs"] == ["act_456"]
    assert result["resolved_source_refs"] == ["rbk_123"]
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False
