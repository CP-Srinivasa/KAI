"""L2 evidence evaluation core (KAI Sprint 2, B-003 autocorrelation-robust).

The fee/mempool series is SLOW and highly autocorrelated. A naive IID bootstrap or
hit-rate would manufacture significance (the Mai-contamination class). This module
provides the robust primitives the shadow evaluation needs:

  * :func:`moving_block_bootstrap_p_mean_positive` — P(mean > 0) by resampling
    CONTIGUOUS blocks (preserving autocorrelation), not independent points.
  * :func:`pit_join` — fail-closed point-in-time join: a measurement is paired only
    with its uniquely identified, direction-compatible outcome inside a bounded
    time window (no look-ahead leakage or duplicated effective samples).
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
DEFAULT_PIT_JOIN_MAX_AGE_SECONDS = 300.0
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
    *,
    max_age_seconds: float = DEFAULT_PIT_JOIN_MAX_AGE_SECONDS,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Join uniquely identified measurements and outcomes without sample inflation.

    Both records must carry the same non-empty ``candidate_id``, the same ``symbol``,
    and compatible ``direction`` (measurement) / ``side`` (outcome). Candidate IDs
    that occur more than once on either side are ambiguous and therefore unmatched.
    The outcome must occur at/after the measurement and no more than
    ``max_age_seconds`` later. Missing provenance, direction, symbol, or valid
    timestamps is fail-closed and remains unmatched.

    The 300-second default is the repository's existing aligned-evidence tolerance;
    callers may make it stricter, but cannot disable the age bound. Returned pairs
    are ordered by outcome time so the downstream moving-block bootstrap preserves
    chronology.
    """
    max_age = float(max_age_seconds)
    if not math.isfinite(max_age) or max_age <= 0.0:
        raise ValueError("max_age_seconds must be finite and > 0")

    measurements_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    outcomes_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for measurement in measurements:
        candidate_id = measurement.get("candidate_id")
        if isinstance(candidate_id, str) and candidate_id.strip():
            measurements_by_id[candidate_id.strip()].append(measurement)
    for outcome in outcomes:
        candidate_id = outcome.get("candidate_id")
        if isinstance(candidate_id, str) and candidate_id.strip():
            outcomes_by_id[candidate_id.strip()].append(outcome)

    dated_pairs: list[tuple[datetime, dict[str, Any], dict[str, Any]]] = []
    for candidate_id, candidate_measurements in measurements_by_id.items():
        candidate_outcomes = outcomes_by_id.get(candidate_id, [])
        if len(candidate_measurements) != 1 or len(candidate_outcomes) != 1:
            continue
        measurement = candidate_measurements[0]
        outcome = candidate_outcomes[0]

        symbol = measurement.get("symbol")
        if not isinstance(symbol, str) or not symbol or outcome.get("symbol") != symbol:
            continue
        direction = measurement.get("direction")
        side = outcome.get("side")
        if direction not in {"long", "short"} or side != direction:
            continue

        measured_at = _parse_ts(measurement.get("ts"))
        outcome_at = _parse_ts(outcome.get("entry_ts"))
        if measured_at is None or outcome_at is None:
            continue
        age_seconds = (outcome_at - measured_at).total_seconds()
        if age_seconds < 0.0 or age_seconds > max_age:
            continue
        dated_pairs.append((outcome_at, measurement, outcome))

    dated_pairs.sort(key=lambda item: item[0])
    return [(measurement, outcome) for _, measurement, outcome in dated_pairs]


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

    Measurements whose feature value is recorded as an explicit ``null`` (producer
    logs "source unavailable", e.g. fee endpoint down) carry no information for the
    split — they are excluded and counted honestly in ``n_null_feature``.
    """
    high: list[float] = []
    low: list[float] = []
    n_null_feature = 0
    for m, o in pairs:
        if o.get("net_bps") is None:
            continue
        feat = m.get(feature_key, 0.5)
        if feat is None:
            n_null_feature += 1
            continue
        (high if float(feat) > 0.5 else low).append(float(o["net_bps"]))
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
        "n_null_feature": n_null_feature,
        "mean_high": mean_high,
        "mean_low": mean_low,
        "p_high_positive": p_high,
        "p_low_positive": p_low,
        "direction": direction,
    }


__all__ = [
    "DEFAULT_PIT_JOIN_MAX_AGE_SECONDS",
    "MIN_SAMPLE",
    "evaluate_feature_direction",
    "moving_block_bootstrap_p_mean_positive",
    "pit_join",
]
