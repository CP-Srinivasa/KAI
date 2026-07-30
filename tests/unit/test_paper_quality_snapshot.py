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


# ── P0-Truth-Repair 2026-07-30 ───────────────────────────────────────────────


def test_missing_trade_pnl_is_excluded_not_realized_fallback(tmp_path: Path) -> None:
    """TL-003-Klasse: kumulatives realized_pnl_usd darf NIE als Trade-PnL
    einfliessen — Zeilen ohne trade_pnl_usd werden ausgeschlossen + gezählt."""
    audit = tmp_path / "paper_execution_audit.jsonl"
    rows = [
        {
            "event_type": "position_closed",
            "symbol": "ETH/USDT",
            "reason": "take",
            "trade_pnl_usd": 341.08,
            "realized_pnl_usd": 341.08,
        },
        # Legacy-Zeile ohne trade_pnl_usd, aber mit riesigem Kumulativ-Wert —
        # floss frueher als +1977.92 "Trade" in by_symbol (unmoegliches Asset-PnL).
        {
            "event_type": "position_closed",
            "symbol": "ADA/USDT",
            "reason": "stop",
            "realized_pnl_usd": 1977.92,
        },
    ]
    _write_audit(audit, rows)
    snap = build_paper_quality_snapshot(audit_path=audit)
    assert snap.rows_missing_trade_pnl == 1
    assert "ADA/USDT" not in snap.by_symbol  # fail-closed: nicht gezählt
    assert abs(snap.sum_trade_pnl_usd - 341.08) < 1e-9
    assert snap.win_rate == 1.0  # 1 win / 1 decided (ADA zählt nicht als loss)
    assert snap.pnl_basis == "trade_pnl_usd_fail_closed"
    # Kumulativ bleibt als explizit-kumulativer Kontext erhalten:
    assert snap.latest_realized_pnl_usd == 1977.92


def test_epoch_scope_cuts_at_last_portfolio_epoch_reset(tmp_path: Path) -> None:
    audit = tmp_path / "paper_execution_audit.jsonl"
    rows = [
        {
            "event_type": "position_closed",
            "symbol": "OLD/USDT",
            "reason": "stop",
            "trade_pnl_usd": -999.0,
        },
        {
            "event_type": "portfolio_epoch_reset",
            "timestamp_utc": "2026-07-12T22:22:09+00:00",
            "new_epoch_id": "paper_v2_attested",
        },
        {
            "event_type": "position_closed",
            "symbol": "NEW/USDT",
            "reason": "take",
            "trade_pnl_usd": 5.0,
        },
    ]
    _write_audit(audit, rows)

    scoped = build_paper_quality_snapshot(audit_path=audit)
    assert scoped.epoch_scoped is True
    assert scoped.epoch_start_utc == "2026-07-12T22:22:09+00:00"
    assert scoped.closures_total == 1  # OLD gehört zum INVALID-Legacy-Buch
    assert "OLD/USDT" not in scoped.by_symbol
    assert abs(scoped.sum_trade_pnl_usd - 5.0) < 1e-9

    unscoped = build_paper_quality_snapshot(audit_path=audit, epoch_scope=False)
    assert unscoped.closures_total == 2
    assert "OLD/USDT" in unscoped.by_symbol
