"""Tests for realized volatility / vol-class / ATR z-score."""

from __future__ import annotations

import math

import pytest

from app.analysis.indicators.realized_volatility import (
    classify_vol_quantile,
    compute_atr_zscore,
    compute_log_returns,
    compute_realized_volatility,
)


# ── log returns ─────────────────────────────────────────────────────────────


def test_log_returns_first_position_is_none() -> None:
    out = compute_log_returns([100.0, 101.0, 102.0])
    assert out[0] is None
    assert out[1] is not None
    assert out[2] is not None


def test_log_returns_constant_close_yields_zero() -> None:
    out = compute_log_returns([100.0] * 5)
    assert out[0] is None
    assert all(r == 0.0 for r in out[1:])


def test_log_returns_protects_against_non_positive_prices() -> None:
    out = compute_log_returns([100.0, 0.0, 100.0, -5.0, 100.0])
    assert out[0] is None
    assert out[1] is None  # log(0) protected
    assert out[2] is None  # log(100/0) — prev was zero
    assert out[3] is None  # negative price
    assert out[4] is None  # prev was negative


# ── realized volatility ─────────────────────────────────────────────────────


def test_rv_rejects_window_below_two() -> None:
    with pytest.raises(ValueError, match="window must be >= 2"):
        compute_realized_volatility([1.0, 2.0, 3.0], window=1)


def test_rv_returns_all_none_when_input_too_short() -> None:
    out = compute_realized_volatility([100.0, 101.0], window=5)
    assert out == [None, None]


def test_rv_warmup_none_then_finite_after_window() -> None:
    closes = [100.0 + i for i in range(10)]
    out = compute_realized_volatility(closes, window=4)
    # Need at least window+1 closes for first RV; it lands at index `window`.
    assert out[0] is None
    assert out[3] is None
    assert out[4] is not None


def test_rv_constant_price_yields_zero() -> None:
    out = compute_realized_volatility([100.0] * 30, window=24)
    finite = [v for v in out if v is not None]
    assert finite, "expected at least one finite RV"
    assert all(v == 0.0 for v in finite)


def test_rv_lifts_during_volatility_burst() -> None:
    # 30 quiet bars (small noise), then a few large jumps.
    quiet = [100.0 + (0.01 if i % 2 == 0 else -0.01) for i in range(30)]
    burst = [110.0, 95.0, 108.0, 92.0, 105.0]
    closes = quiet + burst
    out = compute_realized_volatility(closes, window=10)

    # Take the RV right at the end of the quiet window vs. inside the burst.
    quiet_rv = out[29]
    burst_rv = out[-1]
    assert quiet_rv is not None and burst_rv is not None
    assert burst_rv > quiet_rv * 5, (
        f"burst RV {burst_rv:.4f} should dwarf quiet RV {quiet_rv:.4f}"
    )


# ── vol class ──────────────────────────────────────────────────────────────


def test_classify_vol_quantile_three_buckets() -> None:
    reference = [float(i) for i in range(1, 101)]  # uniform 1..100
    assert classify_vol_quantile(10.0, reference) == "vol_low"
    assert classify_vol_quantile(50.0, reference) == "vol_normal"
    assert classify_vol_quantile(90.0, reference) == "vol_high"


def test_classify_vol_quantile_empty_reference_defaults_to_normal() -> None:
    assert classify_vol_quantile(42.0, []) == "vol_normal"


def test_classify_vol_quantile_rejects_out_of_order_thresholds() -> None:
    with pytest.raises(ValueError, match="require 0"):
        classify_vol_quantile(1.0, [1.0, 2.0], low_pct=80, high_pct=20)


# ── ATR z-score ─────────────────────────────────────────────────────────────


def test_atr_zscore_rejects_window_below_two() -> None:
    with pytest.raises(ValueError, match="window must be >= 2"):
        compute_atr_zscore([1.0, 2.0], window=1)


def test_atr_zscore_warmup_none_until_prior_window_full() -> None:
    series: list[float | None] = [None, None, None] + [1.0, 1.1, 1.2, 1.3, 1.4, 1.5]
    out = compute_atr_zscore(series, window=3)
    # Indices 3..5 build the prior window; index 6 is the first with full prior.
    assert out[3] is None
    assert out[4] is None
    assert out[5] is None
    assert out[6] is not None


def test_atr_zscore_zero_when_atr_is_constant() -> None:
    series: list[float | None] = [None] * 5 + [2.0] * 30
    out = compute_atr_zscore(series, window=10)
    finite = [v for v in out if v is not None]
    assert finite
    assert all(v == 0.0 for v in finite), "constant ATR must yield zero z-score"


def test_atr_zscore_lifts_during_burst() -> None:
    # 30 ATR samples around 1.0, then a single 5.0 spike.
    series: list[float | None] = [None] * 5 + [1.0 + 0.01 * (i % 3) for i in range(30)] + [5.0]
    out = compute_atr_zscore(series, window=20)
    last = out[-1]
    assert last is not None
    assert last > 3.0, f"ATR spike must produce large positive z-score, got {last:.2f}"


def test_atr_zscore_handles_log_invariant() -> None:
    # Sanity: simply produces a finite value when math is well-defined.
    series: list[float | None] = [None] * 3 + [float(i) for i in range(1, 21)]
    out = compute_atr_zscore(series, window=10)
    last = out[-1]
    assert last is not None
    assert math.isfinite(last)
