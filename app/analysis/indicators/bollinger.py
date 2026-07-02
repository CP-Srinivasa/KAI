"""Bollinger band z-score — pure function.

The Bollinger z-score (a.k.a. %b rescaled) measures how many rolling standard
deviations the current close sits from its rolling mean:

    z[i] = (close[i] - mean(W)) / sample_std(W)

where ``W`` is the ``window`` closes ending at index i (inclusive). Sample
standard deviation (n - 1 in the denominator), consistent with
``realized_volatility``. A flat window (std == 0) yields 0.0. Causal: index i
uses only closes[i - window + 1 .. i].

Output is aligned to input length; warm-up positions [0, window - 1) are None.
"""

from __future__ import annotations

import math

BOLLINGER_DEFAULT_WINDOW = 20


def compute_bollinger_z(
    closes: list[float],
    window: int = BOLLINGER_DEFAULT_WINDOW,
) -> list[float | None]:
    """Compute the rolling Bollinger z-score aligned to ``closes``.

    Args:
        closes: ordered close prices (oldest first).
        window: rolling window length. Must be >= 2.

    Returns:
        list with len(closes) entries. None until ``window`` closes are
        available; floats thereafter (0.0 on a flat window).

    Raises:
        ValueError: window < 2.
    """
    if window < 2:
        raise ValueError("window must be >= 2")
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < window:
        return out

    for i in range(window - 1, n):
        w = closes[i - window + 1 : i + 1]
        mean = sum(w) / window
        var = sum((x - mean) ** 2 for x in w) / (window - 1)  # sample variance
        if var == 0.0:
            out[i] = 0.0
            continue
        out[i] = (closes[i] - mean) / math.sqrt(var)
    return out
