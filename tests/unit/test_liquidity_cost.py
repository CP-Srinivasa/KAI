"""Unit tests for turnover-tiered per-symbol cost realism."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.market_data.models import OHLCV
from app.research.liquidity_cost import (
    TURNOVER_TIERS_BPS,
    cost_map_for_series,
    daily_turnover_usd,
    tiered_cost_bps,
)

_T0 = datetime(2026, 6, 1, tzinfo=UTC)


def _candles(n: int, *, close: float, volume: float, interval_h: int = 1) -> list[OHLCV]:
    return [
        OHLCV(
            symbol="X/USDT",
            timestamp_utc=(_T0 + timedelta(hours=i * interval_h)).isoformat(),
            timeframe="1h",
            open=close,
            high=close,
            low=close,
            close=close,
            volume=volume,
        )
        for i in range(n)
    ]


def test_daily_turnover_scales_sample_to_per_day_rate() -> None:
    # 24x 1h candles, close=100, volume=10 -> 24_000 quote over exactly one day.
    assert daily_turnover_usd(_candles(24, close=100.0, volume=10.0), 3600) == 24_000.0
    # Half a day of candles at the same rate -> same per-day rate.
    assert daily_turnover_usd(_candles(12, close=100.0, volume=10.0), 3600) == 24_000.0
    assert daily_turnover_usd([], 3600) == 0.0
    assert daily_turnover_usd(_candles(5, close=1.0, volume=1.0), 0) == 0.0


def test_tiered_cost_adds_liquidity_surcharge() -> None:
    base = 15.0
    assert tiered_cost_bps(200e6, base) == base  # BTC class: venue floor only
    assert tiered_cost_bps(50e6, base) == base + 10.0
    assert tiered_cost_bps(5e6, base) == base + 25.0
    assert tiered_cost_bps(1e6, base) == base + 50.0
    assert tiered_cost_bps(0.0, base) == base + 50.0


def test_tier_boundaries_are_inclusive() -> None:
    base = 0.0
    for floor, surcharge in TURNOVER_TIERS_BPS:
        assert tiered_cost_bps(floor, base) == surcharge


def test_cost_map_for_series() -> None:
    series = {
        "BIG/USDT": _candles(24, close=1000.0, volume=5000.0),  # 120M/day
        "THIN/USDT": _candles(24, close=1.0, volume=1000.0),  # 24k/day
    }
    m = cost_map_for_series(series, 3600, 20.0)
    assert m["BIG/USDT"] == 20.0
    assert m["THIN/USDT"] == 70.0
