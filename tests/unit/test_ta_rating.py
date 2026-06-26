"""Tests for ta_rating — the ToS-compliant TradingView-rating substitute (G4).

A directional technical rating computed from our OWN OHLCV (no scraping, no key):
SMA(short) vs SMA(long) trend + a Wilder-RSI bias → a signed score in [-1, +1] and
a label. Pure + deterministic; used only as an informational cross-check signal
(zero sizing impact).
"""

from __future__ import annotations

from collections.abc import Sequence

from app.market_data.models import OHLCV
from app.market_data.ta_rating import (
    TaRating,
    compute_rsi,
    compute_sma,
    compute_ta_rating,
)


def _candles(closes: Sequence[float]) -> list[OHLCV]:
    return [
        OHLCV(
            symbol="X",
            timestamp_utc=f"2026-06-{i + 1:02d}T00:00:00Z",
            timeframe="1d",
            open=c,
            high=c,
            low=c,
            close=c,
            volume=1000.0,
        )
        for i, c in enumerate(closes)
    ]


class TestPrimitives:
    def test_sma_insufficient_returns_none(self) -> None:
        assert compute_sma([1.0, 2.0], period=5) is None

    def test_sma_is_mean_of_last_n(self) -> None:
        assert compute_sma([1.0, 2.0, 3.0, 4.0], period=2) == 3.5  # mean of last two

    def test_rsi_all_up_is_high(self) -> None:
        rsi = compute_rsi([float(x) for x in range(1, 20)], period=14)
        assert rsi is not None
        assert rsi > 99.0  # only gains → RSI saturates near 100

    def test_rsi_all_down_is_low(self) -> None:
        rsi = compute_rsi([float(x) for x in range(20, 1, -1)], period=14)
        assert rsi is not None
        assert rsi < 1.0

    def test_rsi_insufficient_returns_none(self) -> None:
        assert compute_rsi([1.0, 2.0, 3.0], period=14) is None


class TestRating:
    def test_insufficient_returns_none(self) -> None:
        assert compute_ta_rating(_candles([100.0, 101.0])) is None

    def test_uptrend_is_bullish(self) -> None:
        r = compute_ta_rating(_candles([float(100 + i) for i in range(40)]))
        assert r is not None
        assert isinstance(r, TaRating)
        assert r.score > 0.0
        assert r.trend == "up"
        assert r.label in ("buy", "strong_buy")

    def test_downtrend_is_bearish(self) -> None:
        r = compute_ta_rating(_candles([float(140 - i) for i in range(40)]))
        assert r is not None
        assert r.score < 0.0
        assert r.trend == "down"
        assert r.label in ("sell", "strong_sell")

    def test_score_bounded(self) -> None:
        r = compute_ta_rating(_candles([float(100 + i * 5) for i in range(40)]))
        assert r is not None
        assert -1.0 <= r.score <= 1.0

    def test_unsorted_candles_handled(self) -> None:
        # reversed input still yields the same uptrend verdict (sorted internally)
        candles = list(reversed(_candles([float(100 + i) for i in range(40)])))
        r = compute_ta_rating(candles)
        assert r is not None
        assert r.trend == "up"
