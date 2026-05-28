"""Premium-Signal Analytics (2026-05-28 /goal).

Verhalten der Auswertungs-Schicht (Kapital, PnL, Targets, Entry, Source-Quality)
gegen Normal-, Rand- und Fehlerfälle. Fokus auf die Goal-Akzeptanzkriterien:
fehlende Daten werden NIE erfunden, sondern sauber als ``None`` + Note/Status
sichtbar gemacht.
"""

from __future__ import annotations

from app.observability.premium_signal_analytics import (
    classify_source_quality,
    derive_signal_analytics,
)
from app.observability.premium_signal_trail import build_trail

# ── Builders ─────────────────────────────────────────────────────────────────


def _payload(**extra) -> dict:
    base = {
        "symbol": "TESTUSDT",
        "display_symbol": "TEST/USDT",
        "side": "buy",
        "direction": "long",
        "entry_value": 1.0,
        "stop_loss": 0.95,
        "targets": [1.05, 1.10, 1.15],
        "leverage": 10,
    }
    base.update(extra)
    return base


def _buy_fill(ts: str, *, price: float, qty: float, cash=None, fee=None) -> dict:
    ev = {
        "event_type": "order_filled",
        "timestamp_utc": ts,
        "filled_at": ts,
        "side": "buy",
        "status": "filled",
        "fill_price": price,
        "quantity": qty,
    }
    if cash is not None:
        ev["portfolio_cash"] = cash
    if fee is not None:
        ev["fee_usd"] = fee
    return ev


def _sell_fill(ts: str, *, price: float, qty: float) -> dict:
    return {
        "event_type": "order_filled",
        "timestamp_utc": ts,
        "side": "sell",
        "status": "filled",
        "fill_price": price,
        "quantity": qty,
    }


def _closed(ts: str, *, reason: str, pnl: float, exit_price=None) -> dict:
    ev = {
        "event_type": "position_closed",
        "timestamp_utc": ts,
        "reason": reason,
        "trade_pnl_usd": pnl,
    }
    if exit_price is not None:
        ev["exit_price"] = exit_price
    return ev


def _derive(**kw):
    defaults = {
        "payload": _payload(),
        "source": "telegram_premium_channel",
        "received_at": "2026-05-18T19:00:00+00:00",
        "overall": "OPEN",
        "realized_pnl_usd": None,
        "paper_events": [],
        "bridge_history": [],
    }
    defaults.update(kw)
    return derive_signal_analytics(**defaults)


# ── Kapital ──────────────────────────────────────────────────────────────────


def test_invested_capital_and_percentage():
    fill = _buy_fill("2026-05-18T19:00:05+00:00", price=2.0, qty=50.0, cash=9000.0, fee=0.0)
    a = _derive(overall="OPEN", paper_events=[fill])
    assert a.invested_capital == 100.0  # 2.0 * 50
    assert a.available_capital_at_entry == 9100.0  # 9000 + 100 + 0
    assert a.invested_capital_pct == round(100.0 / 9100.0 * 100, 2)
    assert a.capital_base_note is None


def test_invested_capital_includes_fee_in_base():
    fill = _buy_fill("2026-05-18T19:00:05+00:00", price=1.0, qty=100.0, cash=8000.0, fee=5.0)
    a = _derive(paper_events=[fill])
    assert a.invested_capital == 100.0
    assert a.available_capital_at_entry == 8105.0  # 8000 + 100 + 5


def test_missing_portfolio_cash_marks_base_unavailable():
    fill = _buy_fill("2026-05-18T19:00:05+00:00", price=1.0, qty=100.0)  # no cash
    a = _derive(paper_events=[fill])
    assert a.invested_capital == 100.0
    assert a.available_capital_at_entry is None
    assert a.invested_capital_pct is None
    assert a.capital_base_note == "capital_base_unavailable"


def test_zero_capital_base_no_percentage():
    # cash_after + cost == 0  → base non-positive → kein erfundener Prozentwert
    fill = _buy_fill("2026-05-18T19:00:05+00:00", price=1.0, qty=100.0, cash=-100.0, fee=0.0)
    a = _derive(paper_events=[fill])
    assert a.invested_capital == 100.0
    assert a.invested_capital_pct is None
    assert a.capital_base_note == "capital_base_non_positive"


def test_no_entry_fill_no_capital():
    a = _derive(overall="NOT_APPROVED", paper_events=[], bridge_history=[])
    assert a.invested_capital is None
    assert a.capital_base_note == "no_entry_fill"


def test_bridge_fill_fallback_for_invested():
    bridge = [
        {"stage": "filled", "fill_price": 1.5, "quantity": 40.0, "ts": "2026-05-18T19:00:10+00:00"}
    ]
    a = _derive(overall="OPEN", paper_events=[], bridge_history=bridge)
    assert a.invested_capital == 60.0  # 1.5 * 40
    assert a.available_capital_at_entry is None  # Bridge hat kein portfolio_cash
    assert a.capital_base_note == "capital_base_unavailable"


# ── Entry-Status / Wartezeit ─────────────────────────────────────────────────


def test_entry_on_time():
    fill = _buy_fill("2026-05-18T19:00:07+00:00", price=1.0, qty=100.0, cash=9000.0)
    a = _derive(received_at="2026-05-18T19:00:00+00:00", paper_events=[fill])
    assert a.entry_status == "entered_on_time"
    assert a.entry_delay_seconds == 7
    assert a.entry_delay_label == "sofort"
    assert a.actual_entry_price == 1.0
    assert a.planned_entry_value == 1.0


def test_entry_waited_when_pending_stage_present():
    fill = _buy_fill("2026-05-18T19:01:00+00:00", price=1.0, qty=100.0, cash=9000.0)
    bridge = [
        {"stage": "pending", "ts": "2026-05-18T19:00:30+00:00"},
        {"stage": "filled", "ts": "2026-05-18T19:01:00+00:00"},
    ]
    a = _derive(received_at="2026-05-18T19:00:00+00:00", paper_events=[fill], bridge_history=bridge)
    assert a.entry_status == "waited_for_entry"


def test_entry_late():
    fill = _buy_fill("2026-05-18T21:00:00+00:00", price=1.0, qty=100.0, cash=9000.0)
    a = _derive(received_at="2026-05-18T19:00:00+00:00", paper_events=[fill])
    assert a.entry_status == "entered_late"
    assert a.entry_delay_seconds == 7200
    assert a.entry_delay_label == "nach 2 Std"


def test_entry_missed_on_expired():
    a = _derive(overall="EXPIRED", paper_events=[], bridge_history=[])
    assert a.entry_status == "missed_entry"
    assert a.entry_delay_label == "Einstieg verfehlt"
    assert a.actual_entry_price is None


# ── Targets ──────────────────────────────────────────────────────────────────


def test_targets_hit_via_exit_price():
    fill = _buy_fill("2026-05-18T19:00:05+00:00", price=1.0, qty=100.0, cash=9000.0)
    closed = _closed("2026-05-18T21:00:00+00:00", reason="tp_tier", pnl=12.0, exit_price=1.16)
    a = _derive(
        overall="CLOSED",
        realized_pnl_usd=12.0,
        paper_events=[fill, closed],
        paper_close_reason="tp_tier",
    )
    statuses = [t.status for t in a.targets]
    assert statuses == ["hit", "hit", "hit"]  # 1.16 >= alle Targets
    assert a.targets[0].hit_at == "2026-05-18T21:00:00+00:00"


def test_targets_partial_hit_then_missed_on_stop_loss():
    fill = _buy_fill("2026-05-18T19:00:05+00:00", price=1.0, qty=100.0, cash=9000.0)
    # erst TP1-Teilverkauf @1.06, dann SL-Close @0.95
    sell = _sell_fill("2026-05-18T20:00:00+00:00", price=1.06, qty=50.0)
    closed = _closed("2026-05-18T21:00:00+00:00", reason="stop_loss", pnl=-2.0, exit_price=0.95)
    a = _derive(
        overall="CLOSED",
        realized_pnl_usd=-2.0,
        paper_events=[fill, sell, closed],
        paper_close_reason="stop_loss",
    )
    statuses = [t.status for t in a.targets]
    assert statuses == ["hit", "missed", "missed"]  # nur TP1 (1.05) erreicht


def test_targets_pending_when_open():
    fill = _buy_fill("2026-05-18T19:00:05+00:00", price=1.0, qty=100.0, cash=9000.0)
    a = _derive(overall="OPEN", paper_events=[fill])
    assert [t.status for t in a.targets] == ["pending", "pending", "pending"]


def test_targets_unknown_when_never_entered():
    a = _derive(overall="NOT_APPROVED", paper_events=[], bridge_history=[])
    assert [t.status for t in a.targets] == ["unknown", "unknown", "unknown"]


def test_targets_unknown_when_closed_tp_without_price_evidence():
    # tp_tier-Close ohne exit_price/sell-fills → ehrlich "unknown", nicht "missed"
    fill = _buy_fill("2026-05-18T19:00:05+00:00", price=1.0, qty=100.0, cash=9000.0)
    closed = _closed("2026-05-18T21:00:00+00:00", reason="tp_tier", pnl=10.0)  # kein exit_price
    a = _derive(
        overall="CLOSED",
        realized_pnl_usd=10.0,
        paper_events=[fill, closed],
        paper_close_reason="tp_tier",
    )
    assert [t.status for t in a.targets] == ["unknown", "unknown", "unknown"]


def test_no_targets_empty_list():
    a = _derive(payload=_payload(targets=[]), overall="OPEN")
    assert a.targets == []


# ── Trade-Ergebnis ───────────────────────────────────────────────────────────


def test_result_win_with_pnl_pct():
    fill = _buy_fill("2026-05-18T19:00:05+00:00", price=1.0, qty=100.0, cash=9000.0)
    a = _derive(
        overall="CLOSED", realized_pnl_usd=20.0, paper_events=[fill], paper_close_reason="tp_tier"
    )
    assert a.trade_result_status == "win"
    assert a.final_pnl_usd == 20.0
    assert a.final_pnl_pct == 20.0  # 20 / 100 * 100


def test_result_loss():
    fill = _buy_fill("2026-05-18T19:00:05+00:00", price=1.0, qty=100.0, cash=9000.0)
    a = _derive(
        overall="CLOSED",
        realized_pnl_usd=-15.0,
        paper_events=[fill],
        paper_close_reason="stop_loss",
    )
    assert a.trade_result_status == "loss"
    assert a.final_pnl_pct == -15.0


def test_result_break_even():
    fill = _buy_fill("2026-05-18T19:00:05+00:00", price=1.0, qty=100.0, cash=9000.0)
    a = _derive(overall="CLOSED", realized_pnl_usd=0.0, paper_events=[fill])
    assert a.trade_result_status == "break_even"


def test_result_unknown_when_pnl_missing():
    # pre-V4.1 Close ohne per-Trade-PnL → kein erfundenes win/loss
    a = _derive(overall="CLOSED", realized_pnl_usd=None)
    assert a.trade_result_status == "unknown"
    assert a.final_pnl_usd is None
    assert a.final_pnl_pct is None


def test_result_cancelled_for_rejected():
    for ov in ("BRIDGE_REJECTED", "PAPER_REJECTED", "SOURCE_SKIPPED", "NOT_APPROVED", "EXPIRED"):
        a = _derive(overall=ov)
        assert a.trade_result_status == "cancelled", ov


def test_result_open_for_pending_entry():
    a = _derive(overall="PENDING_ENTRY")
    assert a.trade_result_status == "open"


# ── Signal-Typ (internal vs external) ────────────────────────────────────────


def test_signal_type_external_default():
    assert _derive(source="telegram_premium_channel").signal_type == "external"


def test_signal_type_internal_marker():
    assert _derive(source="internal_signal_gen").signal_type == "internal"
    assert _derive(source="signal_generator_v2").signal_type == "internal"


# ── Source-Quality ───────────────────────────────────────────────────────────


def test_source_quality_unknown_small_sample():
    status, reason = classify_source_quality(
        n_total=2, n_entered=2, n_win=1, n_loss=0, n_missed_entry=0
    )
    assert status == "unknown"
    assert "zu wenig" in reason


def test_source_quality_unknown_few_resolved():
    status, _ = classify_source_quality(n_total=8, n_entered=8, n_win=1, n_loss=0, n_missed_entry=0)
    assert status == "unknown"  # nur 1 entschiedener Trade


def test_source_quality_good():
    status, reason = classify_source_quality(
        n_total=8, n_entered=8, n_win=8, n_loss=0, n_missed_entry=0
    )
    assert status == "good"
    assert "Trefferquote" in reason


def test_source_quality_weak():
    status, _ = classify_source_quality(n_total=6, n_entered=6, n_win=1, n_loss=5, n_missed_entry=0)
    assert status == "weak"


# ── Analyse-Hinweise ─────────────────────────────────────────────────────────


def test_hint_missed_entry():
    a = _derive(overall="EXPIRED")
    assert any("Einstieg verfehlt" in h for h in a.analysis_hints)


def test_hint_high_capital():
    # 60 invested von 100 base → 60% → Warnhinweis
    fill = _buy_fill("2026-05-18T19:00:05+00:00", price=1.0, qty=60.0, cash=40.0, fee=0.0)
    a = _derive(paper_events=[fill])
    assert a.invested_capital_pct == 60.0
    assert any("Kapitalanteil hoch" in h for h in a.analysis_hints)


# ── Robustheit / incomplete data ─────────────────────────────────────────────


def test_incomplete_data_does_not_crash():
    # leerer Payload, kaputte Felder, None-werte
    a = derive_signal_analytics(
        payload={"targets": [None, "x", 1.05], "entry_value": "n/a"},
        source=None,
        received_at=None,
        overall="UNKNOWN",
        realized_pnl_usd=None,
        paper_events=[{"event_type": "order_filled", "side": "buy"}],  # keine Preise
        bridge_history=[{"stage": "weird"}],
    )
    assert a.invested_capital is None
    assert a.planned_entry_value is None
    # nur das eine valide Target bleibt
    assert len(a.targets) == 1
    assert a.targets[0].target_price == 1.05


def test_to_dict_is_json_safe():
    fill = _buy_fill("2026-05-18T19:00:05+00:00", price=1.0, qty=100.0, cash=9000.0)
    a = _derive(
        overall="CLOSED", realized_pnl_usd=5.0, paper_events=[fill], paper_close_reason="tp_tier"
    )
    d = a.to_dict()
    assert {
        "signal_type",
        "invested_capital",
        "invested_capital_pct",
        "entry_status",
        "trade_result_status",
        "targets",
        "source_quality_status",
        "analysis_hints",
    }.issubset(d.keys())
    assert isinstance(d["targets"], list)
    assert isinstance(d["targets"][0], dict)


# ── Integration: build_trail attached analytics + source-quality 2nd pass ─────


def _env(env_id: str, ts: str, source: str = "telegram_premium_channel") -> dict:
    return {
        "timestamp_utc": ts,
        "message_type": "signal",
        "stage": "accepted",
        "status": "ok",
        "source": source,
        "envelope_id": env_id,
        "payload": _payload(),
    }


def _approved(approved_id: str, origin_id: str, ts: str) -> dict:
    return {
        "timestamp_utc": ts,
        "message_type": "signal",
        "stage": "accepted",
        "status": "ok",
        "source": "telegram_premium_channel_approved",
        "envelope_id": approved_id,
        "origin_envelope_id": origin_id,
        "approved_by": "auto-fill",
        "payload": _payload(),
    }


def test_build_trail_attaches_analytics_and_serializes():
    env = _env("ENV-A", "2026-05-18T19:00:00+00:00")
    approved = _approved("ENV-A-app", "ENV-A", "2026-05-18T19:00:01+00:00")
    paper = [
        {
            "event_type": "order_filled",
            "timestamp_utc": "2026-05-18T19:00:05+00:00",
            "filled_at": "2026-05-18T19:00:05+00:00",
            "side": "buy",
            "status": "filled",
            "correlation_id": "ENV-A",
            "idempotency_key": "opbridge:ENV-A-app",
            "fill_price": 1.0,
            "quantity": 100.0,
            "portfolio_cash": 9000.0,
        }
    ]
    trail = build_trail(envelope_records=[env, approved], bridge_records=[], paper_records=paper)
    assert len(trail) == 1
    entry = trail[0]
    assert entry.analytics is not None
    assert entry.analytics.invested_capital == 100.0
    assert entry.analytics.entry_status == "entered_on_time"
    # source-quality 2nd pass lief (1 Signal → unknown, nicht der Init-Default)
    assert entry.analytics.source_quality_status == "unknown"
    assert entry.analytics.source_quality_reason != "pending_aggregation"
    d = entry.to_dict()
    assert d["analytics"]["invested_capital"] == 100.0


def test_build_trail_source_quality_good_over_window():
    """5+ Signale einer Quelle mit klaren Wins → 'good' im zweiten Pass."""
    envelopes: list[dict] = []
    paper: list[dict] = []
    for i in range(6):
        eid = f"ENV-{i}"
        aid = f"ENV-{i}-app"
        ts = f"2026-05-{10 + i:02d}T19:00:00+00:00"
        envelopes.append(_env(eid, ts))
        envelopes.append(_approved(aid, eid, f"2026-05-{10 + i:02d}T19:00:01+00:00"))
        fill_ts = f"2026-05-{10 + i:02d}T19:00:05+00:00"
        paper.append(
            {
                "event_type": "order_filled",
                "timestamp_utc": fill_ts,
                "filled_at": fill_ts,
                "side": "buy",
                "status": "filled",
                "correlation_id": eid,
                "idempotency_key": f"opbridge:{aid}",
                "fill_price": 1.0,
                "quantity": 100.0,
                "portfolio_cash": 9000.0,
            }
        )
        paper.append(
            {
                "event_type": "position_closed",
                "timestamp_utc": f"2026-05-{10 + i:02d}T21:00:00+00:00",
                "correlation_id": eid,
                "reason": "tp_tier",
                "trade_pnl_usd": 10.0,
                "exit_price": 1.16,
            }
        )
    trail = build_trail(
        envelope_records=envelopes, bridge_records=[], paper_records=paper, limit=20
    )
    assert len(trail) == 6
    assert all(e.analytics.trade_result_status == "win" for e in trail)
    assert all(e.analytics.source_quality_status == "good" for e in trail)
