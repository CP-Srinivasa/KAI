"""Exponential Moving Average (EMA) — pure function.

EMA seeded with the simple mean of the first ``period`` values (a common,
deterministic seeding convention), then recursive smoothing:

    alpha   = 2 / (period + 1)
    seed    = mean(values[0:period])            # at index period - 1
    ema[i]  = values[i] * alpha + ema[i-1] * (1 - alpha)   for i >= period

Output is aligned to input length; warm-up positions [0, period - 1) are None;
the first non-None value (the seed) is at index ``period - 1``. Causal: index i
depends only on values[0..i].
"""

from __future__ import annotations


def compute_ema(values: list[float], period: int) -> list[float | None]:
    """Compute an EMA series aligned to ``values``.

    Args:
        values: ordered series (oldest first).
        period: smoothing period. Must be >= 1.

    Returns:
        list with len(values) entries. None during warm-up; floats thereafter.
        If ``values`` has fewer than ``period`` entries, the entire output is None.

    Raises:
        ValueError: period < 1.
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    n = len(values)
    out: list[float | None] = [None] * n
    if n < period:
        return out

    alpha = 2.0 / (period + 1.0)
    ema = sum(values[:period]) / period
    out[period - 1] = ema
    for i in range(period, n):
        ema = values[i] * alpha + ema * (1.0 - alpha)
        out[i] = ema
    return out
