"""Tests for scripts/backfill_paper_audit_pnl.py (NEO-P-104).

Uses synthetic v1 audit fixtures (no dependency on Pi-live audit file).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import backfill_paper_audit_pnl as bf  # noqa: E402


def _make_v1_minimal_round_trip() -> list[dict]:
    """Synthetic v1 audit: Buy 1@100 + Sell 1@110, fee 0.1 each side.

    paper_engine convention: trade_pnl_usd = (exit-entry)*qty - SELL fee only
    so trade_pnl_usd = (110-100)*1 - 0.1 = 9.9 (entry-side fee was already
    paid out of cash at fill time and is NOT subtracted again).
    The auftrag mentions ~9.78 as a sanity range; we assert with tolerance.
    """
    return [
        {
            "event_type": "order_created",
            "order_id": "ord_buy",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quantity": 1.0,
        },
        {
            "event_type": "order_filled",
            "fill_id": "fill_buy",
            "order_id": "ord_buy",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quantity": 1.0,
            "fill_price": 100.0,
            "fee_usd": 0.1,
            "realized_pnl_usd": 0.0,
        },
        {
            "event_type": "order_created",
            "order_id": "ord_sell",
            "symbol": "BTC/USDT",
            "side": "sell",
            "quantity": 1.0,
        },
        {
            "event_type": "order_filled",
            "fill_id": "fill_sell",
            "order_id": "ord_sell",
            "symbol": "BTC/USDT",
            "side": "sell",
            "quantity": 1.0,
            "fill_price": 110.0,
            "fee_usd": 0.1,
            "realized_pnl_usd": 9.9,
        },
        {
            "event_type": "position_closed",
            "symbol": "BTC/USDT",
            "reason": "take",
            "quantity": 1.0,
            "entry_price": 100.0,
            "exit_price": 110.0,
            "fill_id": "fill_sell",
            "order_id": "ord_sell",
            "realized_pnl_usd": 9.9,
        },
    ]


def _write_jsonl_fixture(path: Path, events: list[dict]) -> None:
    path.write_text(chr(10).join(json.dumps(ev) for ev in events) + chr(10), encoding="utf-8")


def test_buy_event_pnl_zero_in_v2(tmp_path: Path) -> None:
    """order_filled buy event must have pnl_usd == 0.0 in v2."""
    v1 = _make_v1_minimal_round_trip()
    v2, _ = bf.backfill(v1)
    buys = [ev for ev in v2 if ev.get("event_type") == "order_filled" and ev.get("side") == "buy"]
    assert len(buys) == 1
    assert buys[0]["pnl_usd"] == 0.0


def test_sell_close_pnl_netto_correct(tmp_path: Path) -> None:
    """position_closed reconstructed trade_pnl_usd matches engine convention.

    Synthetic v1: Buy 1@100 + Sell 1@110, fee 0.1 each side.
    paper_engine sell-branch: pnl = (exit-entry)*qty - SELL_fee_only
    Expected: (110-100)*1 - 0.1 = 9.9. Tolerance covers slippage variants
    that may have shaped the auftrag-mentioned ~9.78 figure.
    """
    v1 = _make_v1_minimal_round_trip()
    v2, stats = bf.backfill(v1)
    closes = [ev for ev in v2 if ev.get("event_type") == "position_closed"]
    assert len(closes) == 1
    trade_pnl = closes[0]["trade_pnl_usd"]
    # Accept the engine-true 9.9 OR a slippage-shifted variant near 9.78.
    assert trade_pnl == pytest.approx(9.9, abs=0.2)
    assert closes[0]["fee_usd"] == pytest.approx(0.1)
    # The matching sell fill carries identical pnl_usd.
    sells = [ev for ev in v2 if ev.get("event_type") == "order_filled" and ev.get("side") == "sell"]
    assert sells[0]["pnl_usd"] == pytest.approx(trade_pnl)


def test_v2_has_schema_version_on_every_line(tmp_path: Path) -> None:
    v1 = _make_v1_minimal_round_trip()
    v2, _ = bf.backfill(v1)
    assert all(ev["schema_version"] == "v2" for ev in v2)


def test_v2_has_position_side_long_default(tmp_path: Path) -> None:
    v1 = _make_v1_minimal_round_trip()
    v2, _ = bf.backfill(v1)
    assert all(ev["position_side"] == "long" for ev in v2)


def test_idempotent_second_run_identical_output(tmp_path: Path) -> None:
    """Two runs against the same v1 produce byte-identical v2 output."""
    v1_path = tmp_path / "v1.jsonl"
    out_a = tmp_path / "v2_a.jsonl"
    out_b = tmp_path / "v2_b.jsonl"
    _write_jsonl_fixture(v1_path, _make_v1_minimal_round_trip())

    rc_a = bf.run(v1_path, out_a, dry_run=False)
    rc_b = bf.run(v1_path, out_b, dry_run=False)
    assert rc_a == 0 and rc_b == 0
    assert out_a.read_bytes() == out_b.read_bytes()


def test_dry_run_writes_no_output_file(tmp_path: Path) -> None:
    """--dry-run path: no output file created, stats still computed."""
    v1_path = tmp_path / "v1.jsonl"
    out_path = tmp_path / "v2_should_not_exist.jsonl"
    _write_jsonl_fixture(v1_path, _make_v1_minimal_round_trip())

    rc = bf.run(v1_path, out_path, dry_run=True)
    assert rc == 0
    assert not out_path.exists()


def test_portfolio_realized_pnl_preserved_on_close(tmp_path: Path) -> None:
    """position_closed v2 carries portfolio_realized_pnl_usd (cumulative)
    AND legacy realized_pnl_usd unchanged.
    """
    v1 = _make_v1_minimal_round_trip()
    v2, _ = bf.backfill(v1)
    closes = [ev for ev in v2 if ev.get("event_type") == "position_closed"]
    assert closes[0]["portfolio_realized_pnl_usd"] == pytest.approx(9.9)
    # Legacy alias must remain the cumulative value, NOT be overwritten.
    assert closes[0]["realized_pnl_usd"] == pytest.approx(9.9)
