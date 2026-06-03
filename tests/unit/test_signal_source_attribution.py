"""NEO-P-20260603-001: signal-source attribution on fills/closes + edge by_source.

Behavior-focused (kai-testing-regeln): we assert that the source attribution
PROPAGATES order_filled -> position_closed, that the default path stays
byte-compatible with pre-attribution audit rows, that edge_report can split a
mixed cohort by source, and that a close without a known entry source degrades
to "unknown" without crashing. We do NOT test private mutation order.
"""

from __future__ import annotations

import json

import pytest

from app.execution import fees
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


# --- engine: source propagates entry fill -> position_closed -------------------


def test_source_propagates_from_entry_fill_to_position_closed(tmp_path):
    """A source/document_id set on create_order must survive onto the
    position_closed event after an auto-close, so closes are attributable."""
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
    )
    fill = engine.fill_order(order, current_price=100.0)
    assert fill is not None

    # Close it (TP-style manual close path) and inspect the audit.
    closed = engine.close_position("BTC/USDT", current_price=120.0, reason="tp")
    assert closed is not None

    rows = _read_jsonl(audit)
    filled = [r for r in rows if r.get("event_type") == "order_filled"]
    closes = [r for r in rows if r.get("event_type") == "position_closed"]

    # Entry fill carries the attribution (via **fill.__dict__).
    entry = filled[0]
    assert entry["source"] == "autonomous_generator"
    assert entry["document_id"] == "doc-real-123"

    # The close event is attributable to the same source/document.
    assert len(closes) == 1
    assert closes[0]["signal_source"] == "autonomous_generator"
    assert closes[0]["document_id"] == "doc-real-123"


def test_default_path_stays_backward_compatible(tmp_path):
    """When no source/document_id is passed, the new fields default to empty
    strings — additive, never None, never crashing legacy consumers."""
    audit = tmp_path / "exec.jsonl"
    engine = PaperExecutionEngine(audit_log_path=str(audit), initial_equity=10_000.0)

    order = engine.create_order(symbol="ETH/USDT", side="buy", quantity=1.0, venue="legacy")
    fill = engine.fill_order(order, current_price=50.0)
    assert fill is not None

    rows = _read_jsonl(audit)
    entry = next(r for r in rows if r.get("event_type") == "order_filled")
    # Present, empty, string — not missing, not None.
    assert entry.get("source") == ""
    assert entry.get("document_id") == ""


# --- edge_report: split a mixed cohort by source -------------------------------


def test_edge_report_groups_by_source_mixed_cohort():
    """A cohort mixing canary_probe and autonomous_generator closes must split
    into distinct by_source buckets so the real generator's edge is isolable."""
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
            "unknown",
            "canary_probe",
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
            "unknown",
            "canary_probe",
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
            "unknown",
            "autonomous_generator",
        ),
    ]
    report = build_edge_report(trades)

    by_source = {c.cohort_key: c for c in report.by_source}
    assert set(by_source) == {"canary_probe", "autonomous_generator"}
    assert by_source["canary_probe"].count == 2
    assert by_source["autonomous_generator"].count == 1
    # to_dict surfaces the new axis.
    assert "by_source" in report.to_dict()


def test_close_without_entry_source_degrades_to_unknown_no_crash():
    """A legacy position_closed row lacking signal_source must parse as
    'unknown' (not crash, not None) and aggregate cleanly into by_source."""
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
            # NOTE: no signal_source / source key (pre-attribution row)
        }
    ]
    trades = parse_closed_trades(events)
    assert len(trades) == 1
    assert trades[0].signal_source == "unknown"

    report = build_edge_report(trades)
    sources = {c.cohort_key for c in report.by_source}
    assert sources == {"unknown"}
