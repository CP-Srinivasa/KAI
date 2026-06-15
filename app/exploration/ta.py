"""Pure technical-analysis helpers for indicator snapshots.

Deterministic functions over a close-price series (oldest→newest). All return
None when there is not enough data, never raise. No external TA dependency —
small, auditable implementations are enough for an exploration snapshot.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence


def sma(closes: Sequence[float], period: int) -> float | None:
    """Simple moving average of the last ``period`` closes."""
    if period <= 0 or len(closes) < period:
        return None
    window = closes[-period:]
    return round(sum(window) / period, 8)


def rsi(closes: Sequence[float], period: int = 14) -> float | None:
    """Wilder's RSI over ``period``. Returns 0–100, or None if too few points."""
    if period <= 0 or len(closes) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for prev, cur in zip(closes[-(period + 1) : -1], closes[-period:], strict=False):
        delta = cur - prev
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def realized_volatility(closes: Sequence[float]) -> float | None:
    """Stdev of simple period-over-period returns, in percent."""
    if len(closes) < 3:
        return None
    returns: list[float] = []
    for prev, cur in zip(closes[:-1], closes[1:], strict=False):
        if prev:
            returns.append((cur - prev) / prev)
    if len(returns) < 2:
        return None
    return round(statistics.pstdev(returns) * 100.0, 4)


def high_low(closes: Sequence[float]) -> tuple[float | None, float | None]:
    """(max, min) of the series, or (None, None) when empty."""
    if not closes:
        return None, None
    return round(max(closes), 8), round(min(closes), 8)
