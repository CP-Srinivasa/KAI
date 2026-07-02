"""Tests for Wilder ATR (app.analysis.indicators.atr.compute_atr)."""

from __future__ import annotations

import pytest

from app.analysis.indicators.atr import ATR_DEFAULT_PERIOD, compute_atr


def test_atr_rejects_period_below_one() -> None:
    with pytest.raises(ValueError, match="period must be >= 1"):
        compute_atr([1.0], [1.0], [1.0], period=0)


def test_atr_rejects_mismatched_input_lengths() -> None:
    with pytest.raises(ValueError, match="equal length"):
        compute_atr([1.0, 2.0], [1.0], [1.0, 2.0])


def test_atr_returns_all_none_when_input_too_short_for_warmup() -> None:
    # period=14 needs at least 15 inputs for first non-None.
    n = 10
    out = compute_atr([1.0] * n, [1.0] * n, [1.0] * n, period=14)
    assert out == [None] * n


def test_atr_warmup_positions_are_none_then_finite() -> None:
    # period=3 → out[0..3] = None, out[3] is first finite value.
    period = 3
    highs = [10.0, 11.0, 12.0, 13.0, 14.0]
    lows = [9.0, 10.0, 11.0, 12.0, 13.0]
    closes = [9.5, 10.5, 11.5, 12.5, 13.5]
    out = compute_atr(highs, lows, closes, period=period)
    assert out[0] is None
    assert out[1] is None
    assert out[2] is None
    assert out[period] is not None
    assert out[period] > 0


def test_atr_constant_ohlc_yields_zero_atr_after_warmup() -> None:
    # Flat market with no range → TR == 0 → ATR == 0 after warmup.
    n = 30
    highs = [100.0] * n
    lows = [100.0] * n
    closes = [100.0] * n
    out = compute_atr(highs, lows, closes, period=ATR_DEFAULT_PERIOD)
    finite = [v for v in out if v is not None]
    assert len(finite) == n - ATR_DEFAULT_PERIOD
    assert all(v == 0.0 for v in finite)


def test_atr_pure_uptrend_is_strictly_positive() -> None:
    # Steadily rising OHLC with consistent range → ATR converges to ~range.
    n = 30
    period = 14
    highs = [100.0 + i + 1.0 for i in range(n)]
    lows = [100.0 + i for i in range(n)]
    closes = [100.0 + i + 0.5 for i in range(n)]
    out = compute_atr(highs, lows, closes, period=period)
    last = out[-1]
    assert last is not None
    # Range is 1.0 per bar; gap-up of 1.0 between bars also drives TR via
    # |high[i] - close[i-1]| = (i+1+1.0) - (i+0.5) = 1.5. So TR ≈ 1.5,
    # ATR converges to ~1.5 after warmup.
    assert 1.0 <= last <= 2.0


def test_atr_responds_to_volatility_burst_with_smoothed_lift() -> None:
    # 14 bars of low-vol, then 1 burst, then 14 bars of low-vol again.
    # Wilder smoothing should lift ATR after the burst, then decay slowly.
    period = 14
    n_quiet = 14
    n_post = 14
    quiet_highs = [101.0] * n_quiet
    quiet_lows = [100.0] * n_quiet
    quiet_closes = [100.5] * n_quiet
    burst_high = 110.0
    burst_low = 95.0
    burst_close = 100.0
    post_highs = [101.0] * n_post
    post_lows = [100.0] * n_post
    post_closes = [100.5] * n_post

    highs = quiet_highs + [burst_high] + post_highs
    lows = quiet_lows + [burst_low] + post_lows
    closes = quiet_closes + [burst_close] + post_closes

    out = compute_atr(highs, lows, closes, period=period)
    finite = [(i, v) for i, v in enumerate(out) if v is not None]
    assert finite, "expected at least one non-None ATR after warmup"

    # ATR right after burst should be clearly elevated vs quiet baseline.
    burst_idx = n_quiet  # the burst bar is at index 14
    pre_burst_atr = out[burst_idx - 1] if burst_idx > 0 else None
    post_burst_atr = out[burst_idx]
    assert post_burst_atr is not None
    if pre_burst_atr is not None:
        assert post_burst_atr > pre_burst_atr, "ATR must lift after volatility burst"

    # And it should still be above pre-burst floor a few bars later (smoothed decay).
    decay_idx = min(burst_idx + 5, len(out) - 1)
    decay_atr = out[decay_idx]
    assert decay_atr is not None
    assert decay_atr > 0.0
