"""Tests für den Churn-/Fee-Effizienz-Report (Operator /goal 2026-06-25).

Deckt ab: gross-vs-net mit ECHTEN Fees, Partials-Inklusion (Red-Team S-001),
FIFO-Haltedauer/Open-Fee-Attribution, Quarantäne-/Implausibilitäts-Ausschluss,
since-Fenster, per-Tag-Kadenz, gross≈0-Fee-Drag-Handling, leerer Stream.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.observability.churn_report import build_churn_report


def _write(tmp_path: Path, events: list[dict]) -> Path:
    p = tmp_path / "audit.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return p


def _open(
    sym: str, qty: float, fee: float, at: str, side: str = "buy", pside: str = "long"
) -> dict:
    return {
        "event_type": "order_filled",
        "symbol": sym,
        "side": side,
        "position_side": pside,
        "filled_quantity": qty,
        "fee_usd": fee,
        "filled_at": at,
    }


def _close(
    sym: str,
    entry: float,
    exit_px: float,
    qty: float,
    fee: float,
    pnl: float,
    at: str,
    reason: str = "take",
    pside: str = "long",
    partial: bool = False,
) -> dict:
    return {
        "event_type": "position_partial_closed" if partial else "position_closed",
        "symbol": sym,
        "position_side": pside,
        "entry_price": entry,
        "exit_price": exit_px,
        "quantity": qty,
        "fee_usd": fee,
        "trade_pnl_usd": pnl,
        "reason": reason,
        "timestamp_utc": at,
    }


def test_basic_gross_net_fee_decomposition(tmp_path: Path) -> None:
    # Long 100 @1.00 -> 1.20 (gross +20), open_fee 10, close_fee 2, trade_pnl 18.
    events = [
        _open("AAA/USDT", 100, 10.0, "2026-06-12T10:00:00+00:00"),
        _close("AAA/USDT", 1.0, 1.2, 100, 2.0, 18.0, "2026-06-12T12:00:00+00:00"),
    ]
    r = build_churn_report(_write(tmp_path, events))
    assert r.available
    assert r.realization_count == 1
    assert r.gross_usd == 20.0  # trade_pnl + close_fee
    assert r.open_fees_usd == 10.0
    assert r.close_fees_usd == 2.0
    assert r.round_trip_fees_usd == 12.0
    assert r.net_usd == 8.0  # gross - rt_fee == trade_pnl - open_fee
    assert r.fee_drag_pct == 60.0  # 12 / 20 * 100
    assert r.hold_minutes_median == 120.0  # 2h
    assert r.hold_under_1h_pct == 0.0


def test_partials_are_counted_with_derived_qty(tmp_path: Path) -> None:
    # Red-Team S-001: TP-Tier-Partials sind reale Realisierungen und MÜSSEN zählen.
    # In den ECHTEN Daten trägt position_partial_closed KEIN quantity (qty=None) →
    # qty wird aus (trade_pnl + close_fee)/price_move abgeleitet. Hier: gross=4.0,
    # price_move=0.1 -> qty=40.
    partial = _close(
        "BBB/USDT",
        1.0,
        1.1,
        40,
        1.0,
        3.0,
        "2026-06-12T11:00:00+00:00",
        reason="tp_tier",
        partial=True,
    )
    partial["quantity"] = None  # reale Audit-Struktur
    events = [
        _open("BBB/USDT", 100, 10.0, "2026-06-12T10:00:00+00:00"),
        partial,
        _close("BBB/USDT", 1.0, 1.2, 60, 1.5, 10.5, "2026-06-12T13:00:00+00:00", reason="take"),
    ]
    r = build_churn_report(_write(tmp_path, events))
    assert r.realization_count == 2
    assert r.partial_count == 1
    assert r.final_close_count == 1
    # Open-Fee (10) wird FIFO über die 100 Einheiten verteilt: 4 auf den Partial
    # (derived qty 40), 6 auf den finalen Close -> Summe bleibt 10, kein Doppel.
    assert round(r.open_fees_usd, 6) == 10.0
    # gross = (3.0+1.0) + (10.5+1.5) = 16.0 ; close_fees = 2.5 ; net = gross-rt
    assert round(r.gross_usd, 6) == 16.0
    assert round(r.close_fees_usd, 6) == 2.5
    assert round(r.net_usd, 6) == round(16.0 - 10.0 - 2.5, 6)
    reasons = {rs.reason for rs in r.by_reason}
    assert "tp_tier" in reasons


def test_implausible_close_excluded(tmp_path: Path) -> None:
    # |exit/entry-1| = 1.0 > 0.40 -> off-market, ausgeschlossen (Alignment-Pop bleibt).
    events = [
        _open("CCC/USDT", 50, 5.0, "2026-06-12T09:00:00+00:00"),
        _close("CCC/USDT", 1.0, 2.0, 50, 1.0, 49.0, "2026-06-12T10:00:00+00:00"),
    ]
    r = build_churn_report(_write(tmp_path, events))
    assert not r.available  # einzige Realisierung wurde ausgeschlossen
    # ... aber mit guard=0 zählt sie:
    r2 = build_churn_report(_write(tmp_path, events), implausible_move_threshold=0.0)
    assert r2.available
    assert r2.realization_count == 1


def test_since_window_filters_but_keeps_pre_open(tmp_path: Path) -> None:
    # Open VOR der Grenze, Close DANACH -> Trade zählt, Haltedauer korrekt.
    events = [
        _open("DDD/USDT", 100, 10.0, "2026-06-10T10:00:00+00:00"),  # vor since
        _close("DDD/USDT", 1.0, 1.2, 100, 2.0, 18.0, "2026-06-12T10:00:00+00:00"),  # nach since
        _open("EEE/USDT", 100, 10.0, "2026-06-09T10:00:00+00:00"),
        _close("EEE/USDT", 1.0, 1.1, 100, 2.0, 8.0, "2026-06-09T12:00:00+00:00"),  # ganz davor
    ]
    r = build_churn_report(_write(tmp_path, events), since="2026-06-11")
    assert r.realization_count == 1  # nur DDD
    assert r.open_fees_usd == 10.0  # DDDs Open-Fee korrekt gematcht trotz Vor-Grenze
    assert r.window_start == "2026-06-12"


def _close_leg(sym: str, qty: float, fee: float, pnl: float, at: str, pside: str = "long") -> dict:
    """order_filled Close-Leg (sell-on-long / buy-on-short) — trägt die Close-Fee.
    In echten Daten existiert dieser Fill IMMER zusätzlich zum position_closed-Event
    (Fee = identisch); die Fee-SPEND-Kadenz zählt ihn (das Event NICHT, sonst doppelt).
    """
    return {
        "event_type": "order_filled",
        "symbol": sym,
        "side": "sell" if pside == "long" else "buy",
        "position_side": pside,
        "filled_quantity": qty,
        "fee_usd": fee,
        "pnl_usd": pnl,
        "filled_at": at,
    }


def test_per_day_cadence(tmp_path: Path) -> None:
    # Realistische Struktur: jeder Close = Close-Leg-Fill (trägt Fee) + Event (PnL).
    events = [
        _open("AAA/USDT", 100, 10.0, "2026-06-12T10:00:00+00:00"),
        _close_leg("AAA/USDT", 100, 2.0, 18.0, "2026-06-12T12:00:00+00:00"),
        _close("AAA/USDT", 1.0, 1.2, 100, 2.0, 18.0, "2026-06-12T12:00:00+00:00"),
        _open("AAA/USDT", 100, 10.0, "2026-06-13T10:00:00+00:00"),
        _close_leg("AAA/USDT", 100, 2.0, 8.0, "2026-06-13T12:00:00+00:00"),
        _close("AAA/USDT", 1.0, 1.1, 100, 2.0, 8.0, "2026-06-13T12:00:00+00:00"),
    ]
    r = build_churn_report(_write(tmp_path, events))
    days = {d.date: d for d in r.per_day}
    assert set(days) == {"2026-06-12", "2026-06-13"}
    assert days["2026-06-12"].fills == 2  # open-leg + close-leg
    assert days["2026-06-12"].realizations == 1
    assert days["2026-06-12"].fee_spend_usd == 12.0  # open 10 + close-leg 2
    assert r.trading_days == 2


def test_empty_stream_unavailable(tmp_path: Path) -> None:
    r = build_churn_report(tmp_path / "missing.jsonl")
    assert not r.available
    assert r.realization_count == 0


def test_gross_near_zero_fee_drag_none(tmp_path: Path) -> None:
    # Brutto exakt 0 (exit==entry) -> Fee-Drag instabil -> None + Flag.
    events = [
        _open("AAA/USDT", 100, 10.0, "2026-06-12T10:00:00+00:00"),
        _close("AAA/USDT", 1.0, 1.0, 100, 0.0, 0.0, "2026-06-12T12:00:00+00:00"),
    ]
    r = build_churn_report(_write(tmp_path, events))
    assert r.gross_near_zero
    assert r.fee_drag_pct is None
    assert r.net_usd == -10.0  # nur die Open-Fee


def test_phantom_untradeable_fees_excluded(tmp_path: Path) -> None:
    """Operator 2026-06-25: Fees auf nicht-handelbaren Symbolen (Self-Pair /
    Stablecoin-Paar) sind fiktiv (nie eine echte gebührenpflichtige Position) und
    müssen aus der ehrlichen Rechnung raus — separat als phantom ausgewiesen."""
    events = [
        # echter Trade (zählt)
        _open("AAA/USDT", 100, 10.0, "2026-06-12T10:00:00+00:00"),
        _close_leg("AAA/USDT", 100, 2.0, 18.0, "2026-06-12T12:00:00+00:00"),
        _close("AAA/USDT", 1.0, 1.2, 100, 2.0, 18.0, "2026-06-12T12:00:00+00:00"),
        # Self-Pair USDT/USDT (Phantom): open-leg 5 + close-leg 2 = 7 fiktive Fees
        _open("USDT/USDT", 100, 5.0, "2026-06-12T10:00:00+00:00"),
        _close_leg("USDT/USDT", 100, 2.0, 8.0, "2026-06-12T12:30:00+00:00"),
        _close("USDT/USDT", 1.0, 1.1, 100, 2.0, 8.0, "2026-06-12T12:30:00+00:00", reason="manual"),
    ]
    r = build_churn_report(_write(tmp_path, events))
    assert r.realization_count == 1  # nur AAA
    assert r.phantom_realization_count == 1
    assert round(r.phantom_fees_usd, 6) == 7.0  # nur order_filled-Legs, kein Event-Doppel
    assert round(r.gross_usd, 6) == 20.0  # AAA 18+2; USDT/USDT raus
    assert round(r.round_trip_fees_usd, 6) == 12.0  # AAA open 10 + close 2; phantom raus
    # Tages-Fee-Kadenz enthält die Phantom-Fees NICHT
    day = {d.date: d for d in r.per_day}["2026-06-12"]
    assert day.fee_spend_usd == 12.0  # nur AAA open10+close2, USDT/USDT-7 ausgeschlossen
