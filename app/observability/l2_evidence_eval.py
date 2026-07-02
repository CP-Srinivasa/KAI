"""L2 evidence evaluation core (KAI Sprint 2, B-003 autocorrelation-robust).

The fee/mempool series is SLOW and highly autocorrelated. A naive IID bootstrap or
hit-rate would manufacture significance (the Mai-contamination class). This module
provides the robust primitives the shadow evaluation needs:

  * :func:`moving_block_bootstrap_p_mean_positive` — P(mean > 0) by resampling
    CONTIGUOUS blocks (preserving autocorrelation), not independent points.
  * :func:`pit_join` — point-in-time join: each measurement is paired only with a
    STRICTLY-later outcome (no look-ahead leakage).
  * :func:`evaluate_feature_direction` — learns whether a raw feature (fee/mempool
    percentile) is contrarian, pro-trend, or has no usable direction — with the
    direction GATED on block-bootstrap confidence, never assumed (B-003).

Read-only analysis; no trading/sizing impact. Trust-promotion stays operator- AND
edge-gated downstream.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

_DEFAULT_RESAMPLES = 5000
MIN_SAMPLE = 8


def moving_block_bootstrap_p_mean_positive(
    values: Sequence[float],
    *,
    block_size: int | None = None,
    n_resamples: int = _DEFAULT_RESAMPLES,
    min_sample: int = MIN_SAMPLE,
    seed: int = 1337,
) -> float | None:
    """P(mean(values) > 0) via a moving-block bootstrap (autocorrelation-robust).

    Resamples contiguous blocks of length ``block_size`` (default ``round(sqrt n)``)
    with replacement until ``n`` points are gathered, then takes the mean — so the
    series' short-range dependence is preserved and the resulting probability is not
    inflated by treating autocorrelated points as independent. ``None`` below
    ``min_sample`` (honest insufficiency).
    """
    vals = [float(v) for v in values]
    n = len(vals)
    if n < min_sample:
        return None
    if block_size is None:
        block_size = max(1, round(math.sqrt(n)))
    block_size = max(1, min(block_size, n))
    max_start = n - block_size
    rng = random.Random(seed)
    positive = 0
    for _ in range(n_resamples):
        sample: list[float] = []
        while len(sample) < n:
            start = rng.randint(0, max_start)
            sample.extend(vals[start : start + block_size])
        if sum(sample[:n]) / n > 0.0:
            positive += 1
    return positive / n_resamples


def _parse_ts(ts: object) -> datetime | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        d = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def pit_join(
    measurements: Sequence[dict[str, Any]],
    outcomes: Sequence[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Point-in-time join: pair each measurement with the EARLIEST outcome for the
    same symbol whose ``entry_ts`` is at/after the measurement ``ts``.

    A strictly-later (or equal) outcome only — an outcome before the measurement is
    look-ahead leakage and is never used. Measurements with no qualifying outcome
    are dropped (honest: not every measurement has a tradeable consequence).
    """
    by_sym: dict[str, list[tuple[datetime, dict[str, Any]]]] = defaultdict(list)
    for o in outcomes:
        sym = o.get("symbol")
        ets = _parse_ts(o.get("entry_ts"))
        if sym is None or ets is None:
            continue
        by_sym[str(sym)].append((ets, o))
    for sym in by_sym:
        by_sym[sym].sort(key=lambda pair: pair[0])

    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for m in measurements:
        sym = m.get("symbol")
        mts = _parse_ts(m.get("ts"))
        if sym is None or mts is None:
            continue
        for ets, o in by_sym.get(str(sym), ()):
            if ets >= mts:
                pairs.append((m, o))
                break
    return pairs


def evaluate_feature_direction(
    pairs: Sequence[tuple[dict[str, Any], dict[str, Any]]],
    *,
    feature_key: str,
    min_sample: int = MIN_SAMPLE,
    seed: int = 1337,
) -> dict[str, Any]:
    """Learn a feature's direction from joined (measurement, outcome) pairs.

    Splits at the median (percentile > 0.5 = "high"), compares the net-bps outcome
    of the high vs low group, and labels the direction ONLY when the block-bootstrap
    confirms both groups (high reliably adverse + low reliably favourable →
    ``contrarian``; the mirror → ``pro_trend``). Otherwise ``inconclusive``; below
    ``min_sample`` per group ``insufficient``. Never assumes a direction (B-003).
    """
    high = [
        float(o["net_bps"])
        for m, o in pairs
        if o.get("net_bps") is not None and float(m.get(feature_key, 0.5)) > 0.5
    ]
    low = [
        float(o["net_bps"])
        for m, o in pairs
        if o.get("net_bps") is not None and float(m.get(feature_key, 0.5)) <= 0.5
    ]
    n_high, n_low = len(high), len(low)
    mean_high = sum(high) / n_high if high else 0.0
    mean_low = sum(low) / n_low if low else 0.0

    p_high: float | None = None
    p_low: float | None = None
    if n_high < min_sample or n_low < min_sample:
        direction = "insufficient"
    else:
        p_high = moving_block_bootstrap_p_mean_positive(high, min_sample=min_sample, seed=seed)
        p_low = moving_block_bootstrap_p_mean_positive(low, min_sample=min_sample, seed=seed)
        high_neg = p_high is not None and p_high < 0.05
        high_pos = p_high is not None and p_high > 0.95
        low_neg = p_low is not None and p_low < 0.05
        low_pos = p_low is not None and p_low > 0.95
        if high_neg and low_pos:
            direction = "contrarian"  # high feature → adverse → fade it
        elif high_pos and low_neg:
            direction = "pro_trend"  # high feature → favourable → follow it
        else:
            direction = "inconclusive"

    return {
        "feature": feature_key,
        "n_high": n_high,
        "n_low": n_low,
        "mean_high": mean_high,
        "mean_low": mean_low,
        "p_high_positive": p_high,
        "p_low_positive": p_low,
        "direction": direction,
    }


__all__ = [
    "MIN_SAMPLE",
    "evaluate_feature_direction",
    "moving_block_bootstrap_p_mean_positive",
    "pit_join",
]
