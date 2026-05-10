"""Market data indicators (ATR, etc.)."""

from __future__ import annotations

from app.market_data.models import OHLCV


def compute_atr(candles: list[OHLCV], period: int = 14) -> float | None:
    """
    Compute Average True Range (ATR) using Wilder's Smoothing.
    Returns None if there are not enough candles.
    """
    if not candles or len(candles) <= period:
        return None

    # Sort by timestamp to ensure chronological order
    sorted_candles = sorted(candles, key=lambda c: c.timestamp_utc)

    true_ranges: list[float] = []
    for i in range(1, len(sorted_candles)):
        current = sorted_candles[i]
        previous = sorted_candles[i - 1]

        tr1 = current.high - current.low
        tr2 = abs(current.high - previous.close)
        tr3 = abs(current.low - previous.close)

        true_range = max(tr1, tr2, tr3)
        true_ranges.append(true_range)

    if len(true_ranges) < period:
        return None

    # First ATR is simple moving average
    atr = sum(true_ranges[:period]) / period

    # Wilder's Smoothing for the rest
    for tr in true_ranges[period:]:
        atr = (atr * (period - 1) + tr) / period

    return atr
