"""Kline backfill window planning — pure functions.

Binance returns at most ``max_limit`` (1000) candles per klines request. To
backfill a historical [start, end] range we must split it into a sequence of
(startTime, limit) request windows. This module computes that plan with no I/O,
so the pagination logic is unit-testable without touching the network.
"""

from __future__ import annotations

_MINUTE_MS = 60_000
_INTERVAL_MS: dict[str, int] = {
    "1m": _MINUTE_MS,
    "5m": 5 * _MINUTE_MS,
    "15m": 15 * _MINUTE_MS,
    "1h": 60 * _MINUTE_MS,
    "4h": 240 * _MINUTE_MS,
    "1d": 1440 * _MINUTE_MS,
}


def interval_to_ms(timeframe: str) -> int:
    """Milliseconds per candle for a supported timeframe.

    Raises:
        ValueError: unsupported timeframe.
    """
    ms = _INTERVAL_MS.get(timeframe)
    if ms is None:
        raise ValueError(f"unsupported timeframe: {timeframe!r}")
    return ms


def plan_kline_windows(
    start_ms: int,
    end_ms: int,
    interval_ms: int,
    max_limit: int = 1000,
) -> list[tuple[int, int]]:
    """Split [start_ms, end_ms] into (startTime, limit) klines request windows.

    Args:
        start_ms: inclusive range start (ms since epoch, a candle open time).
        end_ms: inclusive range end (ms since epoch).
        interval_ms: milliseconds per candle (see :func:`interval_to_ms`).
        max_limit: max candles per request (Binance hard cap 1000).

    Returns:
        Ordered list of (window_start_ms, limit) covering every candle in the
        range exactly once. Empty only if the range contains no candle.

    Raises:
        ValueError: start_ms > end_ms, interval_ms < 1, or max_limit < 1.
    """
    if start_ms > end_ms:
        raise ValueError("start_ms must be <= end_ms")
    if interval_ms < 1:
        raise ValueError("interval_ms must be >= 1")
    if max_limit < 1:
        raise ValueError("max_limit must be >= 1")

    total_bars = (end_ms - start_ms) // interval_ms + 1
    windows: list[tuple[int, int]] = []
    cursor = start_ms
    remaining = total_bars
    while remaining > 0:
        chunk = min(max_limit, remaining)
        windows.append((cursor, chunk))
        cursor += chunk * interval_ms
        remaining -= chunk
    return windows
