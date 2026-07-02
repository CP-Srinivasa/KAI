"""Unit tests for windowed liquidation metrics (#316)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.market_data.liquidation_event import LiquidationEvent
from app.market_data.liquidation_metrics import compute_liquidation_metrics

_NOW = datetime(2026, 6, 19, 12, 0, 0, tzinfo=UTC)


def _ev(
    *,
    offset_s: int,
    side: str,
    notional: float,
    asset: str = "BTC",
    exchange: str = "binance",
) -> LiquidationEvent:
    t = _NOW - timedelta(seconds=offset_s)
    return LiquidationEvent(
        event_id=f"{asset}:{offset_s}:{side}",
        source="binance_forceorder",
        exchange=exchange,
        symbol=f"{asset}USDT",
        asset_id=asset,
        side="SELL" if side == "LONG" else "BUY",
        liquidated_side=side,  # type: ignore[arg-type]
        price=1.0,
        quantity=notional,
        notional_usd=notional,
        event_time=t,
        received_at=t,
        latency_ms=0,
        raw_payload_hash="x",
        confidence=1.0,
        is_snapshot_limited=True,
    )


def test_empty_is_no_data() -> None:
    m = compute_liquidation_metrics([], _NOW)
    assert m.feed_health == "no_data"
    assert m.total_events == 0
    assert m.imbalance_15m is None
    assert m.data_gap_seconds is None
    assert m.window_events == {"1m": 0, "5m": 0, "15m": 0}


def test_windows_long_short_imbalance() -> None:
    events = [
        _ev(offset_s=30, side="LONG", notional=100.0),
        _ev(offset_s=200, side="SHORT", notional=50.0),
        _ev(offset_s=800, side="LONG", notional=20.0, asset="ETH"),
    ]
    m = compute_liquidation_metrics(events, _NOW)

    assert m.window_events == {"1m": 1, "5m": 2, "15m": 3}
    assert m.notional_usd == {"1m": 100.0, "5m": 150.0, "15m": 170.0}
    assert m.events_per_min == 1
    assert m.long_notional_usd_15m == 120.0
    assert m.short_notional_usd_15m == 50.0
    assert m.imbalance_15m == round((120.0 - 50.0) / 170.0, 4)
    assert m.largest_event_usd_15m == 100.0
    # bucket sums per asset across BOTH sides: BTC = 100 LONG + 50 SHORT.
    assert m.asset_bucket_15m == {"BTC": 150.0, "ETH": 20.0}
    assert m.exchange_count_15m == 1
    assert m.feed_health == "ok"
    assert m.data_gap_seconds == 30.0
    assert m.is_snapshot_limited is True


def test_idle_when_last_event_older_than_threshold() -> None:
    m = compute_liquidation_metrics([_ev(offset_s=1200, side="LONG", notional=10.0)], _NOW)
    assert m.feed_health == "idle"  # >900s gap, but events exist historically
    assert m.window_events["15m"] == 0
    assert m.imbalance_15m is None  # nothing inside the 15m window


def test_to_dict_roundtrips_keys() -> None:
    m = compute_liquidation_metrics([_ev(offset_s=10, side="LONG", notional=5.0)], _NOW)
    d = m.to_dict()
    assert d["feed_health"] == "ok"
    assert d["notional_usd"]["1m"] == 5.0
