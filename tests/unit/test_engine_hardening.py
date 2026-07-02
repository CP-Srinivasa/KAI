"""Hardening regression tests (from the security + correctness audits).

Covers the audited failure modes the per-module tests missed: non-finite
(NaN/Infinity) injection, resource bounds, fetch-failure isolation, range
filtering, and off-grid bound snapping.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from app.market_data.binance_adapter import _parse_kline_rows
from app.market_data.history_loader import load_ohlcv_history
from app.market_data.models import OHLCV
from app.research.stats import summarize_net_bps

_H = 3_600_000  # 1h in ms


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def _candle(ms: int) -> OHLCV:
    p = 100.0 + ms / _H
    return OHLCV(
        symbol="BTC/USDT",
        timestamp_utc=_iso(ms),
        timeframe="1h",
        open=p,
        high=p * 1.01,
        low=p * 0.99,
        close=p,
        volume=1.0,
    )


# --- P0: non-finite / inconsistent rows are fail-closed at the trust boundary ---


def test_parse_kline_rows_drops_non_finite_and_inconsistent() -> None:
    # Binance kline rows carry >= 7 fields (trailing field here = close_time).
    rows: list[object] = [
        [0, "100", "101", "99", "100", "5", 0],  # valid
        [_H, float("nan"), "1", "1", "1", "1", 0],  # NaN open
        [2 * _H, float("inf"), "1", "1", "1", "1", 0],  # inf open
        [3 * _H, "100", "100", "200", "100", "1", 0],  # low > min(open,close)
        [4 * _H, "100", "90", "80", "100", "1", 0],  # high < max(open,close)
        [5 * _H, "100", "101", "99", "100", "-5", 0],  # negative volume
        [6 * _H, "0", "1", "1", "0", "1", 0],  # zero price
        [7 * _H, "100", "101", "99", "100", "5", 0],  # valid
    ]
    candles = _parse_kline_rows(rows, "BTC/USDT", "1h")
    assert len(candles) == 2
    assert all(math.isfinite(c.close) and c.close > 0 and c.volume >= 0 for c in candles)


def test_summarize_net_bps_rejects_inf_sample() -> None:
    # An inf-contaminated sample must NOT collapse to a fake p=0.0 "edge".
    s = summarize_net_bps([50.0] * 29 + [float("inf")])
    assert s.p_value == 1.0


def test_summarize_net_bps_rejects_nan_sample() -> None:
    assert summarize_net_bps([10.0, float("nan"), 20.0]).p_value == 1.0


# --- P1: resource bound ---


async def test_load_ohlcv_history_caps_huge_range() -> None:
    async def fetch(symbol: str, timeframe: str, window_start: int, limit: int) -> list[OHLCV]:
        return []

    with pytest.raises(ValueError):
        await load_ohlcv_history("BTC/USDT", "1h", 0, 10_000 * _H, fetch, max_total_bars=100)


# --- P2: a failing window is isolated as a gap, not a crash ---


async def test_failing_window_is_isolated_as_gap() -> None:
    async def fetch(symbol: str, timeframe: str, window_start: int, limit: int) -> list[OHLCV]:
        if window_start == 0:
            raise RuntimeError("transient blip")
        return [
            _candle(window_start + k * _H) for k in range(limit) if window_start + k * _H <= 3 * _H
        ]

    hist = await load_ohlcv_history("BTC/USDT", "1h", 0, 3 * _H, fetch, max_limit=2)
    assert hist.expected_bars == 4
    assert hist.received_bars == 2  # only the surviving window
    assert hist.gap_bars == 2


# --- P2: out-of-range candles are filtered (no exchange-overrun contamination) ---


async def test_out_of_range_candles_are_dropped() -> None:
    async def fetch(symbol: str, timeframe: str, window_start: int, limit: int) -> list[OHLCV]:
        return [_candle(0), _candle(2 * _H)]  # 2H is beyond the requested [0, H]

    hist = await load_ohlcv_history("BTC/USDT", "1h", 0, _H, fetch, max_limit=1000)
    timestamps = [c.timestamp_utc for c in hist.candles]
    assert _iso(0) in timestamps
    assert _iso(2 * _H) not in timestamps


# --- NEO-P2: off-grid bounds snap to the candle grid (no phantom gap) ---


async def test_off_grid_bounds_snap_to_grid() -> None:
    half = _H // 2

    async def fetch(symbol: str, timeframe: str, window_start: int, limit: int) -> list[OHLCV]:
        return [
            _candle(window_start + k * _H) for k in range(limit) if window_start + k * _H <= 2 * _H
        ]

    hist = await load_ohlcv_history("BTC/USDT", "1h", half, 2 * _H + half, fetch, max_limit=1000)
    assert hist.expected_bars == 3  # grid [0, H, 2H], not a phantom 4th
    assert hist.received_bars == 3
    assert hist.gap_bars == 0
