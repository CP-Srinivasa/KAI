from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.ingestion.telegram_channel_envelope import build_envelope_record
from app.ingestion.telegram_channel_parser import parse_premium_channel_message
from app.observability import premium_event_store as store

SAMPLE = """\
Long/Buy #NIGHT/USDT
Entry Point - 3825
Targets: 3845 - 3865 - 3880 - 3900
Leverage - 10x
Stop Loss - 3670"""


def _record():
    parsed = parse_premium_channel_message(SAMPLE)
    assert parsed is not None
    return build_envelope_record(
        parsed,
        chat_id=-1001275462917,
        message_id=23878,
        now=datetime(2026, 5, 30, 13, 43, 52, tzinfo=UTC),
        scale_factor=100000.0,
    )


def test_event_store_persists_signal_and_envelope_with_unique_source_uid(
    tmp_path: Path,
) -> None:
    db = tmp_path / "premium.sqlite3"
    rec = _record()
    store.record_envelope(rec, path=db)
    store.record_envelope(rec, path=db)

    conn = sqlite3.connect(db)
    try:
        signal_count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        envelope_count = conn.execute("SELECT COUNT(*) FROM envelopes").fetchone()[0]
        source_uid = conn.execute("SELECT source_uid FROM signals").fetchone()[0]
    finally:
        conn.close()

    assert signal_count == 1
    assert envelope_count == 1
    assert source_uid == "telegram:-1001275462917:23878"
    assert store.source_uid_exists(source_uid, path=db) is True
    assert store.source_uid_exists("telegram:-1001275462917:99999", path=db) is False


def test_event_store_persists_approval_and_bridge_decision(tmp_path: Path) -> None:
    db = tmp_path / "premium.sqlite3"
    rec = _record()
    approved = {
        **rec,
        "event": "telegram_channel_approval",
        "source": "telegram_premium_channel_approved",
        "envelope_id": "ENV-APP-1",
        "origin_envelope_id": rec["envelope_id"],
        "approved_by": "auto-fill",
    }
    bridge = {
        "timestamp_utc": "2026-05-30T13:44:00+00:00",
        "event": "operator_signal_bridge",
        "envelope_id": "ENV-APP-1",
        "correlation_id": rec["envelope_id"],
        "source_uid": rec["source_uid"],
        "stage": "filled",
        "order_id": "ord-1",
        "fill_id": "fill-1",
        "symbol": "NIGHT/USDT",
        "side": "buy",
        "quantity": 10.0,
        "fill_price": 0.03825,
    }

    store.record_approval(approved, path=db)
    store.record_bridge_decision(bridge, path=db)

    conn = sqlite3.connect(db)
    try:
        approvals = conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
        decisions = conn.execute("SELECT COUNT(*) FROM bridge_decisions").fetchone()[0]
        orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        fills = conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    finally:
        conn.close()

    assert approvals == 1
    assert decisions == 1
    assert orders == 1
    assert fills == 1
