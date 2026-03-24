"""Artifact inventory + retention + cleanup + protected artifact tests (Sprint 25)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.agents.mcp_server import (
    get_artifact_inventory,
    get_artifact_retention_report,
    get_cleanup_eligibility_summary,
    get_protected_artifact_summary,
    get_review_required_summary,
)
from tests.unit.mcp._helpers import _patch_workspace_root


@pytest.mark.asyncio
async def test_get_artifact_inventory_reports_current_and_stale_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    arts_dir = tmp_path / "artifacts"
    arts_dir.mkdir(parents=True)
    current = arts_dir / "report.json"
    stale = arts_dir / "benchmark.json"
    current.write_text("{}", encoding="utf-8")
    stale.write_text("{}", encoding="utf-8")
    old_mtime = stale.stat().st_mtime - 40 * 86400
    os.utime(stale, (old_mtime, old_mtime))

    result = await get_artifact_inventory(
        artifacts_dir="artifacts",
        stale_after_days=30.0,
    )

    assert result["report_type"] == "artifact_inventory"
    assert result["current_count"] == 1
    assert result["stale_count"] == 1


@pytest.mark.asyncio
async def test_get_artifact_retention_report_read_only_invariants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retention report must carry execution_enabled=False, write_back_allowed=False,
    delete_eligible_count=0 (I-154, I-161)."""
    _patch_workspace_root(monkeypatch, tmp_path)
    arts_dir = tmp_path / "artifacts"
    arts_dir.mkdir(parents=True)
    (arts_dir / "mcp_write_audit.jsonl").write_text("{}", encoding="utf-8")
    (arts_dir / "benchmark.json").write_text("{}", encoding="utf-8")

    result = await get_artifact_retention_report(artifacts_dir="artifacts")

    assert result["report_type"] == "artifact_retention_report"
    assert result["execution_enabled"] is False  # I-161
    assert result["write_back_allowed"] is False  # I-161
    assert result["delete_eligible_count"] == 0  # I-154
    assert result["total_count"] == 2


@pytest.mark.asyncio
async def test_get_artifact_retention_report_protected_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit trail and promotion record must be classified as protected."""
    _patch_workspace_root(monkeypatch, tmp_path)
    arts_dir = tmp_path / "artifacts"
    arts_dir.mkdir(parents=True)
    (arts_dir / "mcp_write_audit.jsonl").write_text("{}", encoding="utf-8")
    (arts_dir / "promotion_record.json").write_text("{}", encoding="utf-8")

    result = await get_artifact_retention_report(artifacts_dir="artifacts")

    assert result["protected_count"] == 2
    assert result["rotatable_count"] == 0
    for entry in result["entries"]:
        assert entry["protected"] is True
        assert entry["delete_eligible"] is False  # I-154


@pytest.mark.asyncio
async def test_get_cleanup_eligibility_summary_returns_rotatable_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    arts_dir = tmp_path / "artifacts"
    arts_dir.mkdir(parents=True)
    stale_report = arts_dir / "readiness_report.json"
    stale_report.write_text("{}", encoding="utf-8")
    old_mtime = stale_report.stat().st_mtime - 45 * 86400
    os.utime(stale_report, (old_mtime, old_mtime))

    result = await get_cleanup_eligibility_summary(artifacts_dir="artifacts")

    assert result["report_type"] == "cleanup_eligibility_summary"
    assert result["cleanup_eligible_count"] == 1
    assert result["dry_run_default"] is True
    assert result["delete_eligible_count"] == 0
    assert result["candidates"][0]["path"] == "readiness_report.json"


@pytest.mark.asyncio
async def test_get_protected_artifact_summary_returns_protected_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    arts_dir = tmp_path / "artifacts"
    arts_dir.mkdir(parents=True)
    (arts_dir / "mcp_write_audit.jsonl").write_text("{}", encoding="utf-8")
    (arts_dir / "promotion_record.json").write_text("{}", encoding="utf-8")

    result = await get_protected_artifact_summary(artifacts_dir="artifacts")

    assert result["report_type"] == "protected_artifact_summary"
    assert result["protected_count"] == 2
    protected_paths = {entry["path"] for entry in result["entries"]}
    assert protected_paths == {"mcp_write_audit.jsonl", "promotion_record.json"}


@pytest.mark.asyncio
async def test_get_review_required_summary_returns_review_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    arts_dir = tmp_path / "artifacts"
    arts_dir.mkdir(parents=True)
    (arts_dir / "fresh.json").write_text("{}", encoding="utf-8")

    result = await get_review_required_summary(artifacts_dir="artifacts")

    assert result["report_type"] == "review_required_artifact_summary"
    assert result["review_required_count"] == 1
    assert result["entries"][0]["path"] == "fresh.json"
    assert result["entries"][0]["retention_rationale"]
    assert result["entries"][0]["operator_guidance"]


@pytest.mark.asyncio
async def test_get_artifact_retention_report_blocks_path_outside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    outside = str(tmp_path.parent / "evil_artifacts")

    with pytest.raises(ValueError, match="must stay within workspace"):
        await get_artifact_retention_report(artifacts_dir=outside)
