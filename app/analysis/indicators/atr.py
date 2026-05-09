"""Wilder's Average True Range (ATR) — pure function.

Reference: J. Welles Wilder, "New Concepts in Technical Trading Systems" (1978).

True Range:
    TR[i] = max(high[i] - low[i], |high[i] - close[i-1]|, |low[i] - close[i-1]|)
    TR[0] is undefined (no prior close); out[0] = None.

Smoothing:
    Initial ATR[period] = mean(TR[1..period])
    ATR[i] = (ATR[i-1] * (period - 1) + TR[i]) / period   (Wilder smoothing)

Output:
    list aligned to input length; positions [0, period] are None (warm-up).
    First non-None value is at index `period`.
"""

from __future__ import annotations

ATR_DEFAULT_PERIOD = 14


def compute_atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = ATR_DEFAULT_PERIOD,
) -> list[float | None]:
    """Compute Wilder's ATR series aligned to inputs.

    Args:
        highs: ordered high prices (oldest first).
        lows: ordered low prices, same length as highs.
        closes: ordered close prices, same length.
        period: ATR period, default 14. Must be >= 1.

    Returns:
        list with len(closes) entries. None for warm-up positions [0, period];
        non-negative floats thereafter.

    Raises:
        ValueError: period < 1, or input list lengths mismatch.
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    n = len(closes)
    if len(highs) != n or len(lows) != n:
        raise ValueError("highs, lows, closes must have equal length")
    out: list[float | None] = [None] * n
    if n < period + 1:
        return out

    # True Range for i >= 1; TR[0] not defined.
    trs: list[float] = [0.0]  # placeholder for index 0, never consumed
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)

    # Initial ATR: simple mean of TR[1..period] (indices 1 through period inclusive).
    atr = sum(trs[1 : period + 1]) / period
    out[period] = atr

    # Wilder smoothing for i > period.
    for i in range(period + 1, n):
        atr = (atr * (period - 1) + trs[i]) / period
        out[i] = atr

    return out
