"""2026-06-13: market-regime-at-entry attribution on fills/closes + edge by_regime.

Behavior-focused (kai-testing-regeln): we assert that the regime stamp set at
entry PROPAGATES create_order -> order_filled -> position_closed, that it
SURVIVES an audit replay (the position is reconstructed across a restart so a
close fired many cycles later still carries the entry regime), that the default
path stays byte-compatible with pre-stamp audit rows, and that edge_report can
split a mixed cohort by regime. We do NOT test private mutation order.

Regime-AT-ENTRY is the correct attribution axis ("did signals generated in
regime X have edge"), not regime-at-close — so the value is captured at open and
persisted, never re-derived at close time.
"""

from __future__ import annotations

import json

import pytest

from app.execution import fees
from app.execution.audit_replay import replay_paper_audit
from app.execution.paper_engine import PaperExecutionEngine
from app.observability.edge_report import (
    ClosedTrade,
    build_edge_report,
    parse_closed_trades,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    fees.reset_cache()
    yield
    fees.reset_cache()


def _read_jsonl(path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


# --- engine: regime propagates entry fill -> position_closed -------------------


def test_regime_propagates_from_entry_to_position_closed(tmp_path):
    """A regime set on create_order must survive onto the order_filled AND the
    position_closed event after an auto-close, so closes are regime-attributable."""
    audit = tmp_path / "exec.jsonl"
    engine = PaperExecutionEngine(audit_log_path=str(audit), initial_equity=10_000.0)

    order = engine.create_order(
        symbol="BTC/USDT",
        side="buy",
        quantity=0.1,
        stop_loss=90.0,
        take_profit=120.0,
        venue="legacy",
        source="autonomous_generator",
        document_id="doc-real-123",
        regime="risk_on_trending",
    )
    fill = engine.fill_order(order, current_price=100.0)
    assert fill is not None

    closed = engine.close_position("BTC/USDT", current_price=120.0, reason="tp")
    assert closed is not None

    rows = _read_jsonl(audit)
    entry = next(r for r in rows if r.get("event_type") == "order_filled")
    closes = [r for r in rows if r.get("event_type") == "position_closed"]

    assert entry["regime"] == "risk_on_trending"
    assert len(closes) == 1
    assert closes[0]["regime"] == "risk_on_trending"
    # source attribution still travels alongside (no regression).
    assert closes[0]["signal_source"] == "autonomous_generator"


def test_regime_survives_audit_replay(tmp_path):
    """The whole point of persisting regime on the position: a close fires many
    cycles after entry, often after a process restart. Replaying the order_filled
    row must reconstruct a PaperPosition that still carries the entry regime."""
    audit = tmp_path / "exec.jsonl"
    engine = PaperExecutionEngine(audit_log_path=str(audit), initial_equity=10_000.0)
    order = engine.create_order(
        symbol="ETH/USDT",
        side="buy",
        quantity=1.0,
        venue="legacy",
        source="autonomous_generator",
        regime="risk_off_volatile",
    )
    assert engine.fill_order(order, current_price=50.0) is not None

    # Fresh process: reconstruct portfolio purely from the audit trail.
    result = replay_paper_audit(audit)
    pos = result.positions["ETH/USDT"]
    assert pos.regime == "risk_off_volatile"

    # And a close on the rehydrated engine stamps that surviving regime.
    engine2 = PaperExecutionEngine(audit_log_path=str(audit), initial_equity=10_000.0)
    engine2.rehydrate_from_audit()
    closed = engine2.close_position("ETH/USDT", current_price=55.0, reason="tp")
    assert closed is not None
    closes = [r for r in _read_jsonl(audit) if r.get("event_type") == "position_closed"]
    assert closes[-1]["regime"] == "risk_off_volatile"


def test_default_path_regime_backward_compatible(tmp_path):
    """When no regime is passed, the new field defaults to an empty string —
    additive, never None, never crashing legacy consumers."""
    audit = tmp_path / "exec.jsonl"
    engine = PaperExecutionEngine(audit_log_path=str(audit), initial_equity=10_000.0)

    order = engine.create_order(symbol="ETH/USDT", side="buy", quantity=1.0, venue="legacy")
    assert engine.fill_order(order, current_price=50.0) is not None

    entry = next(r for r in _read_jsonl(audit) if r.get("event_type") == "order_filled")
    assert entry.get("regime") == ""


# --- edge_report: split a mixed cohort by regime -------------------------------


def test_edge_report_groups_by_regime_mixed_cohort():
    """A cohort mixing two regimes must split into distinct by_regime buckets so
    regime-specific edge is isolable instead of collapsing into 'unknown'."""
    trades = [
        ClosedTrade(
            "BTC/USDT",
            "long",
            100.0,
            110.0,
            1.0,
            "tp",
            9.0,
            1.0,
            "2026-06-01T10:00:00+00:00",
            "risk_on_trending",
            "autonomous_generator",
        ),
        ClosedTrade(
            "ETH/USDT",
            "long",
            100.0,
            90.0,
            1.0,
            "sl",
            -11.0,
            1.0,
            "2026-06-01T11:00:00+00:00",
            "risk_off_volatile",
            "autonomous_generator",
        ),
        ClosedTrade(
            "XRP/USDT",
            "long",
            100.0,
            112.0,
            1.0,
            "tp",
            11.0,
            1.0,
            "2026-06-01T12:00:00+00:00",
            "risk_on_trending",
            "autonomous_generator",
        ),
    ]
    report = build_edge_report(trades)

    by_regime = {c.cohort_key: c for c in report.by_regime}
    assert set(by_regime) == {"risk_on_trending", "risk_off_volatile"}
    assert by_regime["risk_on_trending"].count == 2
    assert by_regime["risk_off_volatile"].count == 1


def test_close_without_regime_degrades_to_unknown_no_crash():
    """A legacy position_closed row lacking regime must parse as 'unknown'
    (not crash, not None) and aggregate cleanly into by_regime."""
    events = [
        {
            "event_type": "position_closed",
            "symbol": "LEGACY/USDT",
            "position_side": "long",
            "entry_price": 100.0,
            "exit_price": 105.0,
            "quantity": 1.0,
            "reason": "tp",
            "trade_pnl_usd": 4.8,
            "fee_usd": 0.2,
            "timestamp_utc": "2026-05-01T10:00:00+00:00",
            # NOTE: no regime key (pre-stamp row)
        }
    ]
    trades = parse_closed_trades(events)
    assert len(trades) == 1
    assert trades[0].regime == "unknown"

    report = build_edge_report(trades)
    assert {c.cohort_key for c in report.by_regime} == {"unknown"}
