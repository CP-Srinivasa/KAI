"""Open-Interest change z-score (Goal V5 Phase 2).

Single, verified source for the OI-change-z-score computation, shared by the
Bybit + Binance-Futures adapters so the math is defined once and unit-tested
in isolation (a known series → an expected z).

Definition
==========
Given an OI **time series** ``[oi_0, oi_1, ..., oi_n]`` ordered
oldest→newest, we look at the series of consecutive *changes*
``d_i = oi_i - oi_{i-1}``. The z-score is the **latest** change relative to
the rolling mean/std of all changes in the window::

    z = (d_latest - mean(d)) / std(d)

Why the z-score of *changes*, not of *levels*: ``build_open_interest_evidence``
interprets a positive z as "OI is growing unusually fast right now → fresh
positions entering". The z of the raw level would only say "OI is high vs its
own history", which is a different (and weaker) statement. The change-z is also
naturally **unit-free**, so it is robust to each venue reporting OI in coins vs
USD — exactly the property the design relies on.

Fail-safe / numerical guards
============================
- Fewer than 3 points → cannot form a meaningful change-distribution → 0.0
  (neutral; ``build_open_interest_evidence`` with z=0 yields a tiny, neutral
  contribution rather than a spurious signal).
- Zero variance in the changes (flat or perfectly linear OI) → 0.0 (avoid
  divide-by-zero; "no surprise" maps to a neutral z).
- Any non-finite input is dropped before the computation.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

_MIN_POINTS_FOR_ZSCORE = 3


def oi_change_zscore(series_oldest_first: Sequence[float]) -> float:
    """Z-score of the latest OI change vs the window's change-distribution.

    ``series_oldest_first`` must be ordered oldest → newest. Callers that fetch
    a newest-first venue response must reverse it before calling.
    """
    clean = [float(v) for v in series_oldest_first if _is_finite(v)]
    if len(clean) < _MIN_POINTS_FOR_ZSCORE:
        return 0.0
    changes = [clean[i] - clean[i - 1] for i in range(1, len(clean))]
    if len(changes) < 2:
        return 0.0
    n = len(changes)
    mean = sum(changes) / n
    # Population std over the window (we describe the observed window itself,
    # not infer a larger population — sample-std would only rescale by a
    # constant and is not worth the extra edge-case at n=2).
    var = sum((c - mean) ** 2 for c in changes) / n
    std = math.sqrt(var)
    if std <= 0.0:
        return 0.0
    latest = changes[-1]
    return (latest - mean) / std


def _is_finite(value: object) -> bool:
    try:
        return math.isfinite(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


__all__ = ["oi_change_zscore"]
