from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.observability.shadow_drift import (
    STATUS_OK,
    STATUS_WARN,
    build_shadow_drift_report,
)

NOW = datetime(2026, 6, 8, 9, 0, tzinfo=UTC)


def _write(path: Path, *rows: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )


def _row(hours_ago: float, *, confidence: float, priority: int, rr: float) -> dict:
    return {
        "candidate_id": f"c-{hours_ago}",
        "ts_utc": (NOW - timedelta(hours=hours_ago)).isoformat(),
        "signal_confidence": confidence,
        "recommended_priority": priority,
        "rr": rr,
    }


def test_missing_ledger_warns(tmp_path: Path) -> None:
    report = build_shadow_drift_report(
        ledger_path=tmp_path / "missing.jsonl",
        now=NOW,
        window_hours=24,
    )

    assert report.status == STATUS_WARN
    assert report.rows_in_window == 0
    assert "missing_ledger" in report.reasons
    assert "ledger_growth_below_min" in report.reasons


def test_no_growth_in_window_warns(tmp_path: Path) -> None:
    ledger = tmp_path / "shadow.jsonl"
    _write(ledger, _row(25, confidence=0.6, priority=7, rr=1.2))

    report = build_shadow_drift_report(ledger_path=ledger, now=NOW, window_hours=24)

    assert report.status == STATUS_WARN
    assert report.total_rows == 1
    assert report.rows_in_window == 0
    assert report.latest_ts_utc == (NOW - timedelta(hours=25)).isoformat()
    assert report.reasons == ["ledger_growth_below_min"]


def test_degenerate_feature_variance_warns(tmp_path: Path) -> None:
    ledger = tmp_path / "shadow.jsonl"
    _write(
        ledger,
        *[_row(i, confidence=0.85, priority=10, rr=1.5 + i * 0.1) for i in range(5)],
    )

    report = build_shadow_drift_report(
        ledger_path=ledger,
        now=NOW,
        window_hours=24,
        min_variance_samples=5,
    )

    assert report.status == STATUS_WARN
    assert "feature_degenerate:signal_confidence" in report.reasons
    assert "feature_degenerate:recommended_priority" in report.reasons
    assert "feature_degenerate:rr" not in report.reasons


def test_varied_recent_ledger_is_ok(tmp_path: Path) -> None:
    ledger = tmp_path / "shadow.jsonl"
    _write(
        ledger,
        _row(1, confidence=0.60, priority=7, rr=1.1),
        _row(2, confidence=0.70, priority=8, rr=1.2),
        _row(3, confidence=0.80, priority=9, rr=1.3),
        _row(4, confidence=0.90, priority=10, rr=1.4),
        _row(5, confidence=0.95, priority=11, rr=1.5),
    )

    report = build_shadow_drift_report(
        ledger_path=ledger,
        now=NOW,
        window_hours=24,
        min_variance_samples=5,
    )

    assert report.status == STATUS_OK
    assert report.reasons == []
    assert report.rows_in_window == 5
    assert all(not item.is_degenerate for item in report.feature_variance)
