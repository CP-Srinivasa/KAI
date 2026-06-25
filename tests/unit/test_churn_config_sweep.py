"""Tests for the read-only churn-cooldown counterfactual sweep (Plan PR B).

Behaviour under test (kai-testing-regeln — behaviour, not implementation):
  * FIFO pairing builds one timed round-trip per close, with the EARLIEST matched
    entry as entry_ts; orphan closes (no open) and implausible >40% moves drop.
  * The greedy cooldown replay cuts a re-entry whose entry is < cooldown after the
    last KEPT close of that symbol, and the cut-set net decides helps/hurts.
  * cut_net > 0 → cutting removes net-positive trades → helps() is False.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "churn_config_sweep",
    Path(__file__).resolve().parents[2] / "scripts" / "churn_config_sweep.py",
)
assert _spec and _spec.loader
ccs = importlib.util.module_from_spec(_spec)
# Register before exec so the module's @dataclass fields can resolve __module__
# (dataclasses looks the class' module up in sys.modules).
sys.modules[_spec.name] = ccs
_spec.loader.exec_module(ccs)


def _entry(symbol: str, ts: str, qty: float = 1.0) -> dict:
    return {
        "event_type": "order_filled",
        "side": "buy",
        "position_side": "long",
        "symbol": symbol,
        "filled_quantity": qty,
        "filled_at": ts,
        "fee_usd": 0.1,
    }


def _close(symbol: str, ts: str, net: float, qty: float = 1.0, fee: float = 0.1) -> dict:
    # net == trade_pnl_usd; gross = net + fee = price_move*qty → derive exit_price.
    entry_px = 100.0
    exit_px = entry_px + (net + fee) / qty  # long
    return {
        "event_type": "position_closed",
        "symbol": symbol,
        "position_side": "long",
        "entry_price": entry_px,
        "exit_price": exit_px,
        "quantity": qty,
        "trade_pnl_usd": net,
        "fee_usd": fee,
        "timestamp_utc": ts,
    }


def test_build_round_trips_fifo_pairs_entry_and_close() -> None:
    events = [
        _entry("A/USDT", "2026-06-12T10:00:00+00:00"),
        _close("A/USDT", "2026-06-12T10:30:00+00:00", net=10.0),
        _entry("A/USDT", "2026-06-12T10:40:00+00:00"),
        _close("A/USDT", "2026-06-12T11:00:00+00:00", net=-20.0),
    ]
    rts = ccs.build_round_trips(events, since=None)
    assert len(rts) == 2
    assert rts[0].entry_ts.hour == 10 and rts[0].entry_ts.minute == 0
    assert rts[0].close_ts.minute == 30
    assert rts[0].net_usd == 10.0
    # gross = net + close_fee.
    assert rts[0].gross_usd == 10.1
    assert rts[1].entry_ts.minute == 40 and rts[1].net_usd == -20.0


def test_build_round_trips_skips_orphan_and_implausible() -> None:
    events = [
        # orphan close: no preceding open for ORPH/USDT → dropped.
        _close("ORPH/USDT", "2026-06-12T10:30:00+00:00", net=5.0),
        # implausible >40% move → dropped (entry 100, exit 200 = +100%).
        _entry("WILD/USDT", "2026-06-12T10:00:00+00:00"),
        {
            "event_type": "position_closed",
            "symbol": "WILD/USDT",
            "position_side": "long",
            "entry_price": 100.0,
            "exit_price": 200.0,
            "quantity": 1.0,
            "trade_pnl_usd": 99.9,
            "fee_usd": 0.1,
            "timestamp_utc": "2026-06-12T10:30:00+00:00",
        },
    ]
    assert ccs.build_round_trips(events, since=None) == []


def test_sweep_mixed_cut_set_helps_is_false_when_winners_cut() -> None:
    """A churned loser (A) and a churned winner (B): cooldown 60 cuts both; the
    cut-set net is +10 (>0) → cutting removes net-positive trades → helps False."""
    events = [
        _entry("A/USDT", "2026-06-12T10:00:00+00:00"),
        _close("A/USDT", "2026-06-12T10:30:00+00:00", net=10.0),
        _entry("A/USDT", "2026-06-12T10:40:00+00:00"),  # 10min after A's close
        _close("A/USDT", "2026-06-12T11:00:00+00:00", net=-20.0),
        _entry("B/USDT", "2026-06-12T10:00:00+00:00"),
        _close("B/USDT", "2026-06-12T10:30:00+00:00", net=5.0),
        _entry("B/USDT", "2026-06-12T10:35:00+00:00"),  # 5min after B's close
        _close("B/USDT", "2026-06-12T11:05:00+00:00", net=30.0),
    ]
    rts = ccs.build_round_trips(events, since=None)
    assert len(rts) == 4

    [r60] = ccs.sweep_cooldowns(rts, [60.0])
    assert r60.n_total == 4
    assert r60.n_cut == 2  # both re-entries are within 60min of their prior close
    assert r60.net_total_usd == 25.0  # 10 - 20 + 5 + 30
    assert r60.net_cut_usd == 10.0  # -20 (A) + 30 (B)
    assert r60.net_kept_usd == 15.0  # 10 (A) + 5 (B)
    assert r60.helps is False  # cutting removes a net-positive set → hurts

    # A 1-minute cooldown cuts nothing (both gaps >= 1min).
    [r1] = ccs.sweep_cooldowns(rts, [1.0])
    assert r1.n_cut == 0 and r1.net_cut_usd == 0.0 and r1.helps is False


def test_sweep_detects_helpful_cooldown_when_churn_is_pure_loss() -> None:
    """One symbol, a winner then a fast churned LOSER → cooldown cuts the loser →
    cut-set net is negative → helps True (the only case that justifies tightening)."""
    events = [
        _entry("L/USDT", "2026-06-12T10:00:00+00:00"),
        _close("L/USDT", "2026-06-12T10:30:00+00:00", net=8.0),
        _entry("L/USDT", "2026-06-12T10:35:00+00:00"),  # 5min churn re-entry
        _close("L/USDT", "2026-06-12T11:00:00+00:00", net=-15.0),
    ]
    rts = ccs.build_round_trips(events, since=None)
    [r] = ccs.sweep_cooldowns(rts, [60.0])
    assert r.n_cut == 1
    assert r.net_cut_usd == -15.0
    assert r.cut_mean_net_usd == -15.0
    assert r.helps is True
    assert "HÄRTEN HILFT" in ccs.render([r], since="2026-06-11")


def test_since_filters_by_close_date() -> None:
    events = [
        _entry("A/USDT", "2026-06-05T10:00:00+00:00"),
        _close("A/USDT", "2026-06-05T10:30:00+00:00", net=5.0),  # before cutoff
        _entry("A/USDT", "2026-06-12T10:00:00+00:00"),
        _close("A/USDT", "2026-06-12T10:30:00+00:00", net=7.0),  # in window
    ]
    rts = ccs.build_round_trips(events, since="2026-06-11")
    assert len(rts) == 1 and rts[0].net_usd == 7.0
