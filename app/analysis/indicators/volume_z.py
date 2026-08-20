"""Volume z-score — causal, log-transformed, baseline strictly in the past.

Definition, frozen BEFORE the first hypothesis run (spec
``docs/superpowers/specs/2026-08-19-tv-path-decide-design.md``, B1)::

    mu_t     = fmean( log1p(volume[t-20 ... t-1]) )
    sigma_t  = pstdev( log1p(volume[t-20 ... t-1]) )      # population, ddof = 0
    volume_z_20[t] = ( log1p(volume[t]) - mu_t ) / sigma_t
    SPIKE[t]  <=>  volume_z_20[t] >= 2.0

Three decisions and why they are not arbitrary:

``log1p``
    Volume is strongly right-skewed; single spikes would otherwise dominate both
    mean and standard deviation, and the z-score would mostly measure "was there
    a spike in the last 20 bars", not "is THIS bar a spike".

Baseline excludes the current bar
    The candle must not sit in its own reference distribution — that dampens
    exactly the deviation the feature is supposed to detect. This differs from
    ``funding_align._rolling_z``, which includes the value; there the window is a
    settlement regime, here it is a comparison baseline. The distinction is
    asserted in ``test_volume_z.py`` (a lookahead test must go red if the current
    bar leaks into the baseline).

``pstdev``, and None rather than NaN
    KAI already uses ``statistics.fmean`` + ``statistics.pstdev`` for the funding
    z-score, together with "None on short window and on ``std <= 0``". This module
    takes that semantic verbatim instead of inventing a second convention.

    The None part is correctness, not style: every existing decider guards with
    ``is not None``, and ``NaN is not None`` is **True**. A NaN would sail through
    the guard, make every following comparison False, and the rule would return 0
    — accidentally right, not deliberately. Worse, NaN propagates silently through
    aggregation (``fmean([1, 2, NaN]) -> nan``). The output of this module is
    therefore either a finite float or None; never NaN, never +/-Infinity.
"""

from __future__ import annotations

import math
import statistics

# Baseline length in completed bars. Frozen with the spec; not a tuning knob.
VOLUME_Z_WINDOW = 20
# Spike threshold. Newly set because the manual TradingView rule carries no
# explicit volume threshold (the received payloads hold only ticker + action).
# May only be replaced by an original operator threshold if BOTH its existence
# and its value are shown by timestamped evidence predating the spec — otherwise
# 2.0 is binding. Without that bar, "we found the old threshold" after seeing a
# result would be post-hoc tuning under another name.
VOLUME_SPIKE_Z = 2.0


def compute_volume_z(
    volumes: list[float],
    window: int = VOLUME_Z_WINDOW,
) -> list[float | None]:
    """Causal z-score of ``log1p(volume)`` against the ``window`` PREVIOUS bars.

    Args:
        volumes: ordered bar volumes (oldest first).
        window: number of completed prior bars forming the baseline. Must be >= 2
            (a single point has no dispersion).

    Returns:
        One entry per input volume. A finite float where the baseline is complete
        and has positive dispersion; None during warm-up, on non-finite or
        negative volume anywhere in the baseline or at the bar itself, and where
        the baseline standard deviation is zero.

    Raises:
        ValueError: window < 2.
    """
    if window < 2:
        raise ValueError("window must be >= 2")

    n = len(volumes)
    out: list[float | None] = [None] * n
    # Pre-transform once. Invalid inputs become None here and poison any baseline
    # they belong to, rather than silently contributing a substitute value.
    logs: list[float | None] = []
    for raw in volumes:
        value = _log1p_or_none(raw)
        logs.append(value)

    for i in range(window, n):
        current = logs[i]
        if current is None:
            continue
        baseline = logs[i - window : i]
        if any(value is None for value in baseline):
            continue
        # mypy: the None check above narrows the list, but not for the checker.
        values = [value for value in baseline if value is not None]
        sigma = statistics.pstdev(values)
        if sigma <= 0.0:
            continue
        z = (current - statistics.fmean(values)) / sigma
        if math.isfinite(z):
            out[i] = z
    return out


def _log1p_or_none(raw: float) -> float | None:
    """``log1p(raw)`` for finite, non-negative volume; None for anything else.

    Volume is non-negative by definition; a negative value is a data defect, not
    a small number. Returning None makes the defect visible as warm-up-like
    absence instead of letting ``log1p`` raise or produce NaN.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value < 0.0:
        return None
    return math.log1p(value)
