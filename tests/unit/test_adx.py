"""Tests for Wilder ADX + Plus-DI / Minus-DI (app.analysis.indicators.adx)."""

from __future__ import annotations

import pytest

from app.analysis.indicators.adx import compute_adx_di


def test_adx_rejects_period_below_one() -> None:
    with pytest.raises(ValueError, match="period must be >= 1"):
        compute_adx_di([1.0], [1.0], [1.0], period=0)


def test_adx_rejects_mismatched_input_lengths() -> None:
    with pytest.raises(ValueError, match="equal length"):
        compute_adx_di([1.0, 2.0], [1.0], [1.0, 2.0])


def test_adx_returns_all_none_when_input_too_short_for_di_warmup() -> None:
    n = 5
    result = compute_adx_di([1.0] * n, [1.0] * n, [1.0] * n, period=14)
    assert result.adx == [None] * n
    assert result.plus_di == [None] * n
    assert result.minus_di == [None] * n


def test_adx_di_warmup_then_finite_after_period() -> None:
    period = 3
    n = 12
    highs = [10.0 + i for i in range(n)]
    lows = [9.0 + i for i in range(n)]
    closes = [9.5 + i for i in range(n)]
    result = compute_adx_di(highs, lows, closes, period=period)
    assert result.plus_di[period - 1] is None
    assert result.plus_di[period] is not None
    assert result.minus_di[period] is not None
    # ADX warmup is longer: 2*period - 1 = 5
    assert result.adx[2 * period - 2] is None
    assert result.adx[2 * period - 1] is not None


def test_adx_pure_uptrend_yields_high_adx_and_plus_di_dominant() -> None:
    n = 50
    period = 14
    # Steady uptrend: each bar makes a strict higher high and higher low.
    highs = [100.0 + i + 1.0 for i in range(n)]
    lows = [100.0 + i for i in range(n)]
    closes = [100.0 + i + 0.5 for i in range(n)]
    result = compute_adx_di(highs, lows, closes, period=period)

    last_adx = result.adx[-1]
    last_plus = result.plus_di[-1]
    last_minus = result.minus_di[-1]
    assert last_adx is not None and last_plus is not None and last_minus is not None
    assert last_adx > 50.0, f"pure uptrend should yield high ADX, got {last_adx:.2f}"
    assert last_plus > last_minus, "pure uptrend must have +DI > -DI"
    assert last_minus < 5.0, f"pure uptrend should drive -DI near zero, got {last_minus:.2f}"


def test_adx_pure_downtrend_yields_high_adx_and_minus_di_dominant() -> None:
    n = 50
    period = 14
    highs = [200.0 - i for i in range(n)]
    lows = [200.0 - i - 1.0 for i in range(n)]
    closes = [200.0 - i - 0.5 for i in range(n)]
    result = compute_adx_di(highs, lows, closes, period=period)

    last_adx = result.adx[-1]
    last_plus = result.plus_di[-1]
    last_minus = result.minus_di[-1]
    assert last_adx is not None and last_plus is not None and last_minus is not None
    assert last_adx > 50.0, f"pure downtrend should yield high ADX, got {last_adx:.2f}"
    assert last_minus > last_plus, "pure downtrend must have -DI > +DI"
    assert last_plus < 5.0, f"pure downtrend should drive +DI near zero, got {last_plus:.2f}"


def test_adx_sideways_market_yields_low_adx() -> None:
    # Oscillating sideways: alternating up/down by 1 around 100.
    n = 60
    period = 14
    highs = [101.0 if i % 2 == 0 else 100.5 for i in range(n)]
    lows = [99.0 if i % 2 == 0 else 99.5 for i in range(n)]
    closes = [100.0 + (0.2 if i % 2 == 0 else -0.2) for i in range(n)]
    result = compute_adx_di(highs, lows, closes, period=period)

    last_adx = result.adx[-1]
    assert last_adx is not None
    assert last_adx < 25.0, f"sideways chop should keep ADX low, got {last_adx:.2f}"


def test_adx_handles_zero_tr_without_crash() -> None:
    # Constant OHLC across n bars → TR == 0 → DI calc must not divide-by-zero.
    n = 30
    period = 14
    result = compute_adx_di([100.0] * n, [100.0] * n, [100.0] * n, period=period)
    last_plus = result.plus_di[-1]
    last_minus = result.minus_di[-1]
    last_adx = result.adx[-1]
    assert last_plus == 0.0
    assert last_minus == 0.0
    assert last_adx == 0.0
