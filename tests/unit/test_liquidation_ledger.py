"""Unit tests for the append-only liquidation ledger (#316)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.market_data.liquidation_event import LiquidationEvent
from app.market_data.liquidation_ledger import append_event, load_events

_NOW = datetime(2026, 6, 19, 12, 0, 0, tzinfo=UTC)


def _ev(offset_s: int) -> LiquidationEvent:
    t = _NOW - timedelta(seconds=offset_s)
    return LiquidationEvent(
        event_id=f"id:{offset_s}",
        source="binance_forceorder",
        exchange="binance",
        symbol="BTCUSDT",
        asset_id="BTC",
        side="SELL",
        liquidated_side="LONG",
        price=100.0,
        quantity=1.0,
        notional_usd=100.0,
        event_time=t,
        received_at=t,
        latency_ms=0,
        raw_payload_hash="h",
        confidence=1.0,
        is_snapshot_limited=True,
    )


def test_append_and_load_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "liq.jsonl"
    append_event(_ev(10), p)
    append_event(_ev(20), p)
    loaded = load_events(p)
    assert [e.event_id for e in loaded] == ["id:10", "id:20"]
    assert loaded[0].event_time == _NOW - timedelta(seconds=10)


def test_since_filter(tmp_path: Path) -> None:
    p = tmp_path / "liq.jsonl"
    append_event(_ev(30), p)  # older
    append_event(_ev(5), p)  # newer
    loaded = load_events(p, since=_NOW - timedelta(seconds=10))
    assert [e.event_id for e in loaded] == ["id:5"]


def test_missing_file_is_empty() -> None:
    assert load_events(Path("does/not/exist.jsonl")) == []


def test_bad_line_is_skipped_failopen(tmp_path: Path) -> None:
    p = tmp_path / "liq.jsonl"
    append_event(_ev(10), p)
    with p.open("a", encoding="utf-8") as fh:
        fh.write("{ this is not valid json\n")
    append_event(_ev(5), p)
    loaded = load_events(p)
    assert [e.event_id for e in loaded] == ["id:10", "id:5"]
