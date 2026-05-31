"""V5 loop open-deadlock watchdog coverage (DS-20260531-V5).

The 2026-05-31 incident: the loop ran ~24h of cycles (so freshness + min-cycle
checks stayed green) while EVERY cycle was diversification_rejected — zero
orders opened. This watchdog catches that "loop spins but opens nothing"
signature, distinguishes it from a legitimately full book, and fires
regardless of RE_ENTRY_MODE.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.alerts.health_check import run_health_check_report


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _cycles(status: str, count: int, minutes_ago: int = 5) -> list[dict]:
    ts = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()
    return [{"started_at": ts, "status": status} for _ in range(count)]


def _make_artifacts(tmp_path: Path, cycles: list[dict]) -> Path:
    adir = tmp_path / "artifacts"
    adir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(adir / "trading_loop_audit.jsonl", cycles)
    # alert_audit fresh so the alert-freshness/volume checks don't dominate.
    ts = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    _write_jsonl(
        adir / "alert_audit.jsonl",
        [{"dispatched_at": ts, "actionable": True, "document_id": "doc-1"}],
    )
    return adir


def _deadlock_issue(report) -> object | None:
    for i in report.issues:
        if i.component == "trading_loop_open_deadlock":
            return i
    return None


def test_diversification_deadlock_flagged(tmp_path: Path) -> None:
    """100 cycles all diversification_rejected, 0 completed → critical."""
    adir = _make_artifacts(tmp_path, _cycles("diversification_rejected", 100))
    report = run_health_check_report(artifacts_dir=adir)
    issue = _deadlock_issue(report)
    assert issue is not None
    assert issue.severity == "critical"
    assert "diversification" in issue.message
    assert "0 completed" in issue.message


def test_size_deadlock_flagged(tmp_path: Path) -> None:
    adir = _make_artifacts(tmp_path, _cycles("size_rejected", 100))
    report = run_health_check_report(artifacts_dir=adir)
    assert _deadlock_issue(report) is not None


def test_full_book_not_flagged(tmp_path: Path) -> None:
    """A full book rejects with risk_rejected (max_open) — NOT a deadlock."""
    adir = _make_artifacts(tmp_path, _cycles("risk_rejected", 100))
    report = run_health_check_report(artifacts_dir=adir)
    assert _deadlock_issue(report) is None


def test_healthy_loop_with_completed_not_flagged(tmp_path: Path) -> None:
    """Mixed book that still opens positions (completed > 0) is never a deadlock."""
    cycles = _cycles("diversification_rejected", 90) + _cycles("completed", 10)
    adir = _make_artifacts(tmp_path, cycles)
    report = run_health_check_report(artifacts_dir=adir)
    assert _deadlock_issue(report) is None


def test_below_min_cycles_not_flagged(tmp_path: Path) -> None:
    """Too few cycles to conclude a deadlock (default min_expected_cycles=10)."""
    adir = _make_artifacts(tmp_path, _cycles("diversification_rejected", 5))
    report = run_health_check_report(artifacts_dir=adir)
    assert _deadlock_issue(report) is None


def test_fires_under_re_entry_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unlike priority-saturation, the open-deadlock check is RE_ENTRY_MODE-independent."""
    monkeypatch.setenv("RE_ENTRY_MODE_ENABLED", "true")
    adir = _make_artifacts(tmp_path, _cycles("diversification_rejected", 100))
    report = run_health_check_report(artifacts_dir=adir)
    assert report.re_entry_mode_active is True
    assert _deadlock_issue(report) is not None


def test_mixed_blocking_below_ratio_not_flagged(tmp_path: Path) -> None:
    """Open-blocking gates below the 50% ratio → not a clear open-deadlock."""
    cycles = _cycles("diversification_rejected", 30) + _cycles("risk_rejected", 70)
    adir = _make_artifacts(tmp_path, cycles)
    report = run_health_check_report(artifacts_dir=adir)
    assert _deadlock_issue(report) is None


def test_paper_silence_hint_appended_when_stale(tmp_path: Path) -> None:
    """When paper_execution_audit is stale, the message carries the secondary hint."""
    adir = _make_artifacts(tmp_path, _cycles("diversification_rejected", 100))
    # Write a paper_execution_audit and backdate its mtime to 5h ago.
    paper = adir / "paper_execution_audit.jsonl"
    _write_jsonl(paper, [{"event_type": "position_closed", "symbol": "X/USDT"}])
    import os

    old = (datetime.now(UTC) - timedelta(hours=5)).timestamp()
    os.utime(paper, (old, old))
    report = run_health_check_report(artifacts_dir=adir)
    issue = _deadlock_issue(report)
    assert issue is not None
    assert "paper_execution_audit silent" in issue.message
