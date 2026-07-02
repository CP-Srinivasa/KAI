"""Unit tests for app.observability.paper_quality_snapshot."""

from __future__ import annotations

import json
from pathlib import Path

from app.observability.paper_quality_snapshot import build_paper_quality_snapshot


def _write_audit(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def test_handles_missing_audit_file(tmp_path: Path) -> None:
    snap = build_paper_quality_snapshot(audit_path=tmp_path / "no.jsonl", last_n=10)
    assert snap.closures_total == 0
    assert snap.win_rate == 0.0
    assert snap.latest_realized_pnl_usd is None


def test_aggregates_closures_per_symbol_and_reason(tmp_path: Path) -> None:
    """Mirror the 2026-05-26 trio: ETH stop -276.67, HYPE take +53.25,
    BTC stop -126.37 — gate ≥10 fills says green, quality view says
    negative."""
    audit = tmp_path / "paper_execution_audit.jsonl"
    rows = [
        {
            "event_type": "position_closed",
            "symbol": "ETH/USDT",
            "reason": "stop",
            "trade_pnl_usd": -276.67,
            "realized_pnl_usd": -276.67,
        },
        {
            "event_type": "position_closed",
            "symbol": "HYPE/USDT",
            "reason": "take",
            "trade_pnl_usd": 53.25,
            "realized_pnl_usd": -223.42,
        },
        {
            "event_type": "position_closed",
            "symbol": "BTC/USDT",
            "reason": "stop",
            "trade_pnl_usd": -126.37,
            "realized_pnl_usd": -349.79,
        },
        # Non-close events must be ignored.
        {"event_type": "order_created", "symbol": "BTC/USDT"},
        {"event_type": "order_filled", "symbol": "BTC/USDT"},
    ]
    _write_audit(audit, rows)

    snap = build_paper_quality_snapshot(audit_path=audit, last_n=25)
    assert snap.closures_total == 3
    assert snap.window_last_n == 25
    assert snap.win_rate == 1 / 3  # 1 win out of 3 decided
    assert abs(snap.sum_trade_pnl_usd - (-349.79)) < 1e-2
    assert snap.latest_realized_pnl_usd == -349.79
    assert "ETH/USDT" in snap.by_symbol
    assert snap.by_symbol["ETH/USDT"]["losses"] == 1
    assert snap.by_reason["stop"]["count"] == 2
    assert snap.by_reason["take"]["wins"] == 1


def test_window_last_n_limits_aggregate(tmp_path: Path) -> None:
    audit = tmp_path / "paper_execution_audit.jsonl"
    rows = [
        {
            "event_type": "position_closed",
            "symbol": "BTC/USDT",
            "reason": "stop",
            "trade_pnl_usd": -10.0,
            "realized_pnl_usd": -10.0 * (i + 1),
        }
        for i in range(20)
    ]
    _write_audit(audit, rows)

    snap = build_paper_quality_snapshot(audit_path=audit, last_n=5)
    assert snap.closures_total == 20
    assert len(snap.window_closures) == 5
    assert snap.by_symbol["BTC/USDT"]["count"] == 5
    # latest is last row -> realized_pnl_usd of -200
    assert snap.latest_realized_pnl_usd == -200.0


def test_partial_close_event_is_counted(tmp_path: Path) -> None:
    audit = tmp_path / "paper_execution_audit.jsonl"
    rows = [
        {
            "event_type": "position_partial_closed",
            "symbol": "BTC/USDT",
            "reason": "take",
            "trade_pnl_usd": 12.34,
            "realized_pnl_usd": 12.34,
        }
    ]
    _write_audit(audit, rows)
    snap = build_paper_quality_snapshot(audit_path=audit)
    assert snap.closures_total == 1
    assert snap.by_reason["take"]["wins"] == 1
