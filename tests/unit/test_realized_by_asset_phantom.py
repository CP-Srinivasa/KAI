"""DS-20260529-V1: realized-by-asset excludes phantom closes.

The 2026-05-28 MATIC phantom closes (BitMEX delisted-instrument price 0.40875 vs
real ~0.088, +364%/cycle) must not show up as realized profit in the dashboard's
per-asset view. They are excluded from realized_pnl_usd and surfaced separately
as quarantined_pnl_usd so the operator still sees they were registered.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.execution.portfolio_read import compute_realized_by_asset


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _close(symbol: str, entry: float, exit_: float, pnl: float, ts: str) -> dict:
    return {
        "schema_version": "v2",
        "event_type": "position_closed",
        "timestamp_utc": ts,
        "symbol": symbol,
        "quantity": 1.0,
        "entry_price": entry,
        "exit_price": exit_,
        "position_side": "long",
        "trade_pnl_usd": pnl,
        "fee_usd": 0.0,
    }


def test_phantom_close_excluded_from_realized(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    _write(
        path,
        [
            _close("ETH/USDT", 2000.0, 2100.0, 100.0, "2026-05-29T10:00:00+00:00"),
            # MATIC +364% phantom (exit/entry - 1 = 3.64 > 2.0 cap)
            _close("MATIC/USDT", 0.088, 0.40875, 73458.59, "2026-05-29T11:00:00+00:00"),
        ],
    )
    r = compute_realized_by_asset(path)
    assert r["available"] is True
    by_sym = {b["symbol"]: b for b in r["by_asset"]}

    assert by_sym["ETH/USDT"]["realized_pnl_usd"] == 100.0
    assert by_sym["ETH/USDT"]["quarantined_pnl_usd"] == 0.0

    matic = by_sym["MATIC/USDT"]
    assert matic["realized_pnl_usd"] == 0.0
    assert matic["closed_trades"] == 0
    assert matic["quarantined_closes"] == 1
    assert matic["quarantined_pnl_usd"] == 73458.59

    # Totals reflect the real book, phantom surfaced separately.
    assert r["totals"]["realized_pnl_usd"] == 100.0
    assert r["totals"]["quarantined_pnl_usd"] == 73458.59
    assert r["totals"]["quarantined_closes"] == 1


def test_legit_close_below_cap_kept(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    # +50% is a real winner, must NOT be quarantined.
    _write(path, [_close("SOL/USDT", 100.0, 150.0, 50.0, "2026-05-29T10:00:00+00:00")])
    r = compute_realized_by_asset(path)
    by_sym = {b["symbol"]: b for b in r["by_asset"]}
    assert by_sym["SOL/USDT"]["realized_pnl_usd"] == 50.0
    assert by_sym["SOL/USDT"]["quarantined_closes"] == 0
    assert r["totals"]["quarantined_pnl_usd"] == 0.0


def test_eth_off_market_signature_excluded_from_realized(tmp_path: Path) -> None:
    """2026-06-23 edge-epoch leak: the ETH off-market signature (+55%, UNDER the
    200% phantom cap) leaked into realized PnL and made ETH look like the top
    performer. The exact forensic signature must now quarantine it here too."""
    path = tmp_path / "audit.jsonl"
    _write(
        path,
        [
            _close("ETH/USDT", 2000.0, 2100.0, 100.0, "2026-06-12T10:00:00+00:00"),
            # ETH off-market signature: exit 3259.9692, +55% — phantom guard MISSES it.
            _close("ETH/USDT", 2100.0, 3259.9692, 5643.3, "2026-05-26T20:41:40+00:00"),
        ],
    )
    r = compute_realized_by_asset(path)
    eth = {b["symbol"]: b for b in r["by_asset"]}["ETH/USDT"]
    # The legit +100 close survives; the +5643 off-market fake is quarantined.
    assert eth["realized_pnl_usd"] == 100.0
    assert eth["closed_trades"] == 1
    assert eth["quarantined_closes"] == 1
    assert eth["quarantined_pnl_usd"] == 5643.3
    assert r["totals"]["realized_pnl_usd"] == 100.0
    assert r["totals"]["quarantined_pnl_usd"] == 5643.3
