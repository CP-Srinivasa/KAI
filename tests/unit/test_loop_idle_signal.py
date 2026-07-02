"""Unit tests for app.observability.loop_idle_signal."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.observability.loop_idle_signal import compute_loop_idle_signal


def _write_loop_audit(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _cycle(started: datetime, status: str, symbol: str = "BTC/USDT") -> dict:
    return {
        "cycle_id": f"cyc_{started.isoformat()}",
        "started_at": started.isoformat(),
        "completed_at": started.isoformat(),
        "symbol": symbol,
        "status": status,
        "notes": [],
    }


def test_insufficient_data_when_window_has_too_few_cycles(tmp_path: Path) -> None:
    audit = tmp_path / "trading_loop_audit.jsonl"
    now = datetime(2026, 5, 26, 10, 0, 0, tzinfo=UTC)
    _write_loop_audit(
        audit,
        [_cycle(now - timedelta(minutes=10 * i), "priority_rejected") for i in range(3)],
    )
    result = compute_loop_idle_signal(
        audit_path=audit, window_hours=24, idle_fraction=0.95, min_cycles=6, now_utc=now
    )
    assert result.status == "insufficient_data"
    assert result.total_cycles == 3


def test_idle_when_every_cycle_is_priority_rejected(tmp_path: Path) -> None:
    """Regression 2026-05-26: 66/66 priority_rejected since previous
    afternoon. The signal must flag this even with 0 completed cycles."""
    audit = tmp_path / "trading_loop_audit.jsonl"
    now = datetime(2026, 5, 26, 10, 0, 0, tzinfo=UTC)
    _write_loop_audit(
        audit,
        [_cycle(now - timedelta(minutes=10 * i), "priority_rejected") for i in range(20)],
    )
    result = compute_loop_idle_signal(
        audit_path=audit, window_hours=24, idle_fraction=0.95, min_cycles=6, now_utc=now
    )
    assert result.status == "idle"
    assert result.completed == 0
    assert result.priority_rejected == 20
    assert result.rejection_fraction is not None
    assert result.rejection_fraction >= 0.95


def test_healthy_when_window_has_at_least_one_completed(tmp_path: Path) -> None:
    """The load-bearing clause: any completed cycle keeps the loop
    classified as healthy even if priority_rejected dominates."""
    audit = tmp_path / "trading_loop_audit.jsonl"
    now = datetime(2026, 5, 26, 10, 0, 0, tzinfo=UTC)
    rows = [_cycle(now - timedelta(minutes=10 * i), "priority_rejected") for i in range(19)]
    rows.append(_cycle(now - timedelta(minutes=5), "completed"))
    _write_loop_audit(audit, rows)
    result = compute_loop_idle_signal(
        audit_path=audit, window_hours=24, idle_fraction=0.95, min_cycles=6, now_utc=now
    )
    assert result.status == "healthy"
    assert result.completed == 1


def test_healthy_when_rejection_fraction_below_threshold(tmp_path: Path) -> None:
    audit = tmp_path / "trading_loop_audit.jsonl"
    now = datetime(2026, 5, 26, 10, 0, 0, tzinfo=UTC)
    # 10 priority_rejected + 5 stale_data + 0 completed -> 10/15 = 0.667
    rows = [_cycle(now - timedelta(minutes=10 * i), "priority_rejected") for i in range(10)] + [
        _cycle(now - timedelta(minutes=10 * (10 + i)), "stale_data") for i in range(5)
    ]
    _write_loop_audit(audit, rows)
    result = compute_loop_idle_signal(
        audit_path=audit, window_hours=24, idle_fraction=0.95, min_cycles=6, now_utc=now
    )
    assert result.status == "healthy"
    assert result.rejection_fraction is not None
    assert result.rejection_fraction < 0.95


def test_window_filtering_drops_old_cycles(tmp_path: Path) -> None:
    audit = tmp_path / "trading_loop_audit.jsonl"
    now = datetime(2026, 5, 26, 10, 0, 0, tzinfo=UTC)
    rows = [_cycle(now - timedelta(minutes=10 * i), "priority_rejected") for i in range(10)] + [
        _cycle(now - timedelta(hours=48 + i), "completed")
        for i in range(5)  # outside
    ]
    _write_loop_audit(audit, rows)
    result = compute_loop_idle_signal(
        audit_path=audit, window_hours=24, idle_fraction=0.95, min_cycles=6, now_utc=now
    )
    assert result.total_cycles == 10
    assert result.completed == 0
    assert result.status == "idle"


def test_missing_audit_file_is_insufficient_data(tmp_path: Path) -> None:
    audit = tmp_path / "no_such_file.jsonl"
    result = compute_loop_idle_signal(
        audit_path=audit,
        window_hours=24,
        idle_fraction=0.95,
        min_cycles=6,
        now_utc=datetime.now(UTC),
    )
    assert result.status == "insufficient_data"
    assert result.total_cycles == 0


def test_invalid_idle_fraction_raises(tmp_path: Path) -> None:
    audit = tmp_path / "trading_loop_audit.jsonl"
    audit.touch()
    with pytest.raises(ValueError):
        compute_loop_idle_signal(audit_path=audit, idle_fraction=0.0)
    with pytest.raises(ValueError):
        compute_loop_idle_signal(audit_path=audit, idle_fraction=1.1)


def test_invalid_min_cycles_raises(tmp_path: Path) -> None:
    audit = tmp_path / "trading_loop_audit.jsonl"
    audit.touch()
    with pytest.raises(ValueError):
        compute_loop_idle_signal(audit_path=audit, min_cycles=0)
