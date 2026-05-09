"""Realized Volatility + Volatility-Class + ATR z-score — pure functions.

Realized volatility (RV) is the standard deviation of log returns over a
rolling window, used as a regime indicator: extreme RV signals panic,
suppressed RV signals chop_quiet.

For 1h candles, a 24-bar window covers ~24h.

Volatility classification compares a single RV to a trailing reference
distribution and assigns one of three buckets (vol_low / vol_normal /
vol_high) at the 33rd / 66th percentile by default.

ATR z-score is the standardized deviation of current ATR from a rolling
prior window — z > 1 flags a volatility anomaly that the regime classifier
can use to distinguish breakout from steady trend.
"""

from __future__ import annotations

import math
from typing import Literal

VolClass = Literal["vol_low", "vol_normal", "vol_high"]

RV_DEFAULT_WINDOW = 24  # 24 bars at 1h = 24h
VOL_CLASS_DEFAULT_REFERENCE_BARS = 720  # 30 days at 1h


def compute_log_returns(closes: list[float]) -> list[float | None]:
    """Compute log-returns aligned to ``closes``.

    out[0] is None (no prior close). Non-positive prices yield None at that
    position to keep the log domain safe — caller can treat that as bad data.
    """
    n = len(closes)
    out: list[float | None] = [None] * n
    for i in range(1, n):
        prev = closes[i - 1]
        cur = closes[i]
        if prev <= 0 or cur <= 0:
            out[i] = None
            continue
        out[i] = math.log(cur / prev)
    return out


def compute_realized_volatility(
    closes: list[float],
    window: int = RV_DEFAULT_WINDOW,
) -> list[float | None]:
    """Rolling sample standard deviation of log returns.

    Args:
        closes: ordered close prices (oldest first).
        window: number of returns in the rolling window. Must be >= 2.

    Returns:
        list of len(closes); None until ``window`` log-returns are available;
        non-negative floats thereafter.

    Raises:
        ValueError: window < 2.
    """
    if window < 2:
        raise ValueError("window must be >= 2")
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < window + 1:
        return out

    log_returns = compute_log_returns(closes)
    for i in range(window, n):
        slice_returns = log_returns[i - window + 1 : i + 1]
        valid = [r for r in slice_returns if r is not None]
        if len(valid) < 2:
            out[i] = None
            continue
        m = sum(valid) / len(valid)
        var = sum((r - m) ** 2 for r in valid) / (len(valid) - 1)  # sample std
        out[i] = math.sqrt(var)
    return out


def classify_vol_quantile(
    rv_value: float,
    reference_window: list[float],
    low_pct: float = 33.0,
    high_pct: float = 66.0,
) -> VolClass:
    """Classify a single RV against a trailing reference distribution.

    Args:
        rv_value: the RV to classify.
        reference_window: trailing RV samples (e.g. last 30 days).
        low_pct / high_pct: percentile thresholds in [0, 100].

    Returns:
        ``vol_low`` if rv_value <= low_pct quantile, ``vol_high`` if
        >= high_pct, else ``vol_normal``. Empty reference returns
        ``vol_normal`` (neutral default).
    """
    if not reference_window:
        return "vol_normal"
    if not 0.0 <= low_pct <= high_pct <= 100.0:
        raise ValueError("require 0 <= low_pct <= high_pct <= 100")
    sorted_ref = sorted(reference_window)
    n = len(sorted_ref)
    low_idx = max(0, min(n - 1, int(round((low_pct / 100.0) * (n - 1)))))
    high_idx = max(0, min(n - 1, int(round((high_pct / 100.0) * (n - 1)))))
    low_threshold = sorted_ref[low_idx]
    high_threshold = sorted_ref[high_idx]
    if rv_value <= low_threshold:
        return "vol_low"
    if rv_value >= high_threshold:
        return "vol_high"
    return "vol_normal"


def compute_atr_zscore(
    atr_series: list[float | None],
    window: int = 30,
) -> list[float | None]:
    """Rolling z-score of ATR against the trailing prior window.

    For each index i, the z-score uses the ``window`` most-recent non-None
    ATR samples strictly before i. Index i itself is the "current"
    observation being standardized.

    Args:
        atr_series: aligned ATR series (with leading None warm-up).
        window: rolling window of past ATR samples. Must be >= 2.

    Returns:
        z-score aligned to atr_series. None where the prior window cannot
        be filled with non-None ATR samples.

    Raises:
        ValueError: window < 2.
    """
    if window < 2:
        raise ValueError("window must be >= 2")
    n = len(atr_series)
    out: list[float | None] = [None] * n
    for i in range(n):
        if atr_series[i] is None:
            continue
        prior: list[float] = []
        j = i - 1
        while j >= 0 and len(prior) < window:
            v = atr_series[j]
            if v is not None:
                prior.append(v)
            j -= 1
        if len(prior) < window:
            continue
        m = sum(prior) / len(prior)
        var = sum((v - m) ** 2 for v in prior) / (len(prior) - 1)
        if var == 0.0:
            out[i] = 0.0
            continue
        out[i] = (atr_series[i] - m) / math.sqrt(var)
    return out
