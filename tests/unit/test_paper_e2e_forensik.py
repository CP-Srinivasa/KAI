"""End-to-End Forensik-Test 2026-05-25.

Beweist die vollständige Kette Signal → Order → Fill → Close → realized-by-asset
ohne Live-Mode und ohne Backtest-Endpoint. Reine in-process simulation.

Zweck: harter Nachweis für Go/No-Go-Entscheidung — wenn DIESER Test grün ist,
dann ist die Paper-Pipeline-Mechanik nachweislich funktionsfähig.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.execution.audit_replay import replay_paper_audit
from app.execution.portfolio_read import compute_realized_by_asset


def _write_audit(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_e2e_paper_trade_lifecycle(tmp_path: Path) -> None:
    """Vollständiger Trade-Lifecycle: open → partial → close → realized.

    Akzeptanzkriterium: realized-by-asset zeigt korrekte Aggregation für
    Mehrfach-Symbol Long/Short Trades.
    """
    audit = tmp_path / "audit.jsonl"

    # Multi-asset, multi-trade scenario:
    #   BTC: open 1 @ 70k, close 1 @ 72k  → +2000
    #   ETH: open 10 @ 3k, partial 5 @ 3.2k → +1000, close 5 @ 3.4k → +2000
    #   SOL: open 100 @ 50, close 100 @ 45 → -500
    rows = [
        # BTC trade
        {
            "schema_version": "v2",
            "event_type": "order_created",
            "timestamp_utc": "2026-05-01T10:00:00+00:00",
            "order_id": "ord_btc_open",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quantity": 1.0,
            "order_type": "market",
            "limit_price": None,
            "stop_loss": 68000.0,
            "take_profit": 72000.0,
            "created_at": "2026-05-01T10:00:00+00:00",
            "idempotency_key": "btc_open",
            "status": "pending",
            "risk_check_id": "rck_1",
            "position_side": "long",
            "venue": "paper",
            "correlation_id": "",
            "leverage": None,
            "source": "",
        },
        {
            "schema_version": "v2",
            "event_type": "order_filled",
            "timestamp_utc": "2026-05-01T10:00:01+00:00",
            "fill_id": "fill_btc_open",
            "order_id": "ord_btc_open",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quantity": 1.0,
            "fill_price": 70000.0,
            "fee_usd": 42.0,
            "filled_at": "2026-05-01T10:00:01+00:00",
            "slippage_pct": 0.0,
            "pnl_usd": 0.0,
            "position_side": "long",
            "fee_venue": "paper",
            "fee_role": "taker",
            "fee_bps_applied": 6.0,
            "fee_table_version": "1.0.0",
            "correlation_id": "",
            "portfolio_cash": 30000.0,
            "realized_pnl_usd": 0.0,
        },
        {
            "schema_version": "v2",
            "event_type": "order_filled",
            "timestamp_utc": "2026-05-02T10:00:00+00:00",
            "fill_id": "fill_btc_close",
            "order_id": "ord_btc_close",
            "symbol": "BTC/USDT",
            "side": "sell",
            "quantity": 1.0,
            "fill_price": 72000.0,
            "fee_usd": 43.0,
            "filled_at": "2026-05-02T10:00:00+00:00",
            "slippage_pct": 0.0,
            "pnl_usd": 2000.0,
            "position_side": "long",
            "portfolio_cash": 102000.0,
            "realized_pnl_usd": 2000.0,
        },
        {
            "schema_version": "v2",
            "event_type": "position_closed",
            "timestamp_utc": "2026-05-02T10:00:01+00:00",
            "symbol": "BTC/USDT",
            "quantity": 1.0,
            "trade_pnl_usd": 2000.0,
            "fee_usd": 43.0,
            "realized_pnl_usd": 2000.0,
        },
        # ETH trade with partial close
        {
            "schema_version": "v2",
            "event_type": "order_filled",
            "timestamp_utc": "2026-05-03T10:00:00+00:00",
            "fill_id": "fill_eth_open",
            "order_id": "ord_eth_open",
            "symbol": "ETH/USDT",
            "side": "buy",
            "quantity": 10.0,
            "fill_price": 3000.0,
            "fee_usd": 18.0,
            "position_side": "long",
            "portfolio_cash": 72000.0,
            "realized_pnl_usd": 2000.0,
        },
        {
            "schema_version": "v2",
            "event_type": "position_partial_closed",
            "timestamp_utc": "2026-05-04T10:00:00+00:00",
            "symbol": "ETH/USDT",
            "quantity": 5.0,
            "trade_pnl_usd": 1000.0,
            "fee_usd": 9.6,
        },
        {
            "schema_version": "v2",
            "event_type": "position_closed",
            "timestamp_utc": "2026-05-05T10:00:00+00:00",
            "symbol": "ETH/USDT",
            "quantity": 5.0,
            "trade_pnl_usd": 2000.0,
            "fee_usd": 10.2,
        },
        # SOL trade — losing
        {
            "schema_version": "v2",
            "event_type": "order_filled",
            "timestamp_utc": "2026-05-06T10:00:00+00:00",
            "fill_id": "fill_sol_open",
            "order_id": "ord_sol_open",
            "symbol": "SOL/USDT",
            "side": "buy",
            "quantity": 100.0,
            "fill_price": 50.0,
            "fee_usd": 3.0,
            "position_side": "long",
        },
        {
            "schema_version": "v2",
            "event_type": "position_closed",
            "timestamp_utc": "2026-05-07T10:00:00+00:00",
            "symbol": "SOL/USDT",
            "quantity": 100.0,
            "trade_pnl_usd": -500.0,
            "fee_usd": 2.7,
        },
    ]
    _write_audit(audit, rows)

    # === Replay-Verifikation ===
    r = replay_paper_audit(audit)
    assert r.available is True
    assert r.error is None
    assert r.skipped_events == ()

    # === Realized-by-Asset-Verifikation ===
    by_asset = compute_realized_by_asset(audit)
    assert by_asset["available"] is True
    assert by_asset["totals"]["closed_trades"] == 4
    assert by_asset["totals"]["assets_count"] == 3
    assert by_asset["totals"]["realized_pnl_usd"] == 4500.0  # 2000 + 1000+2000 - 500
    assert by_asset["totals"]["partial_close_events"] == 1
    assert by_asset["totals"]["full_close_events"] == 3

    by_sym = {b["symbol"]: b for b in by_asset["by_asset"]}
    assert by_sym["BTC/USDT"]["realized_pnl_usd"] == 2000.0
    assert by_sym["BTC/USDT"]["closed_trades"] == 1
    assert by_sym["ETH/USDT"]["realized_pnl_usd"] == 3000.0
    assert by_sym["ETH/USDT"]["closed_trades"] == 2
    assert by_sym["ETH/USDT"]["partial_closes"] == 1
    assert by_sym["ETH/USDT"]["full_closes"] == 1
    assert by_sym["SOL/USDT"]["realized_pnl_usd"] == -500.0
    assert by_sym["SOL/USDT"]["losses"] == 1

    # === Top/Worst-Performer ===
    assert by_asset["top_performer"]["symbol"] == "ETH/USDT"
    assert by_asset["top_performer"]["realized_pnl_usd"] == 3000.0
    assert by_asset["worst_performer"]["symbol"] == "SOL/USDT"
    assert by_asset["worst_performer"]["realized_pnl_usd"] == -500.0


def test_e2e_replay_recovers_after_corrupt_event(tmp_path: Path) -> None:
    """Mid-stream corruption darf den Rest des Audit-Streams nicht kosten."""
    audit = tmp_path / "audit.jsonl"
    rows = [
        # Good BTC open + close
        {
            "schema_version": "v2",
            "event_type": "order_filled",
            "timestamp_utc": "2026-05-01T10:00:00+00:00",
            "order_id": "btc_open",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quantity": 1.0,
            "fill_price": 70000.0,
            "position_side": "long",
        },
        {
            "schema_version": "v2",
            "event_type": "order_filled",
            "timestamp_utc": "2026-05-02T10:00:00+00:00",
            "order_id": "btc_close",
            "symbol": "BTC/USDT",
            "side": "sell",
            "quantity": 1.0,
            "fill_price": 72000.0,
            "position_side": "long",
        },
        {
            "schema_version": "v2",
            "event_type": "position_closed",
            "timestamp_utc": "2026-05-02T10:00:01+00:00",
            "symbol": "BTC/USDT",
            "trade_pnl_usd": 2000.0,
        },
        # Now a corruption: try to sell BTC again with no position
        {
            "schema_version": "v2",
            "event_type": "order_filled",
            "timestamp_utc": "2026-05-02T10:00:02+00:00",
            "order_id": "btc_phantom_close",
            "symbol": "BTC/USDT",
            "side": "sell",
            "quantity": 1.0,
            "fill_price": 72000.0,
            "position_side": "long",
        },
        # And a valid trade AFTER the corruption
        {
            "schema_version": "v2",
            "event_type": "order_filled",
            "timestamp_utc": "2026-05-03T10:00:00+00:00",
            "order_id": "eth_open",
            "symbol": "ETH/USDT",
            "side": "buy",
            "quantity": 5.0,
            "fill_price": 3000.0,
            "position_side": "long",
        },
        {
            "schema_version": "v2",
            "event_type": "order_filled",
            "timestamp_utc": "2026-05-04T10:00:00+00:00",
            "order_id": "eth_close",
            "symbol": "ETH/USDT",
            "side": "sell",
            "quantity": 5.0,
            "fill_price": 3100.0,
            "position_side": "long",
        },
        {
            "schema_version": "v2",
            "event_type": "position_closed",
            "timestamp_utc": "2026-05-04T10:00:01+00:00",
            "symbol": "ETH/USDT",
            "trade_pnl_usd": 500.0,
        },
    ]
    _write_audit(audit, rows)
    r = replay_paper_audit(audit)
    assert r.available is True
    # The valid ETH should be present (closed → removed)
    assert "ETH/USDT" not in r.positions
    # The corruption was skipped:
    assert len(r.skipped_events) == 1
    assert "audit_close_without_position" in r.skipped_events[0][1]

    by_asset = compute_realized_by_asset(audit)
    assert by_asset["totals"]["realized_pnl_usd"] == 2500.0  # BTC + ETH
    assert by_asset["totals"]["closed_trades"] == 2
