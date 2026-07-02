"""Wilder's RSI (Relative Strength Index) — pure function.

Reference: J. Welles Wilder, "New Concepts in Technical Trading Systems" (1978).

Formula:
    First avg_gain / avg_loss = simple mean of first `period` deltas.
    Subsequent avg_gain[i] = (avg_gain[i-1] * (period-1) + gain[i]) / period
    (Wilder's smoothing — equivalent to EMA with alpha = 1/period.)
    RS = avg_gain / avg_loss
    RSI = 100 - 100 / (1 + RS)

Output:
    list aligned to input length; positions [0, period] are None (warm-up).
    First non-None value is at index `period`.
"""

from __future__ import annotations

RSI_DEFAULT_PERIOD = 14


def compute_rsi(closes: list[float], period: int = RSI_DEFAULT_PERIOD) -> list[float | None]:
    """Compute Wilder's RSI series aligned to `closes`.

    Args:
        closes: ordered list of close prices (oldest first).
        period: RSI period, default 14. Must be >= 1.

    Returns:
        list with len(closes) entries. None for warm-up; floats in [0, 100] thereafter.
        If `closes` has fewer than `period + 1` entries, the entire output is None.

    Raises:
        ValueError: period < 1.
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < period + 1:
        return out

    deltas = [closes[i] - closes[i - 1] for i in range(1, n)]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    # Initial averages: simple mean of first `period` deltas.
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    out[period] = _rsi_from_avgs(avg_gain, avg_loss)

    # Wilder smoothing for the remainder.
    for i in range(period + 1, n):
        delta_idx = i - 1  # gains/losses index
        avg_gain = (avg_gain * (period - 1) + gains[delta_idx]) / period
        avg_loss = (avg_loss * (period - 1) + losses[delta_idx]) / period
        out[i] = _rsi_from_avgs(avg_gain, avg_loss)

    return out


def _rsi_from_avgs(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))
