"""TV-2 RSI(14) indicator tests.

Golden values verified against the canonical Wilder example from
"New Concepts in Technical Trading Systems" (1978), table 6.1, p. 65.
"""

from __future__ import annotations

import math

import pytest

from app.analysis.indicators import RSI_DEFAULT_PERIOD, compute_rsi

# Wilder's original example: 17 close prices, period=14.
# First RSI value (at index 14) is documented as ~70.53.
_WILDER_CLOSES: list[float] = [
    44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
    45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03,
]


def test_compute_rsi_warm_up_is_none() -> None:
    out = compute_rsi(_WILDER_CLOSES, period=14)
    assert len(out) == len(_WILDER_CLOSES)
    # Indices 0..14 are warm-up (we need period+1 deltas = 15 closes for first value).
    for i in range(14):
        assert out[i] is None
    # Index 14 = first RSI value.
    assert out[14] is not None


def test_compute_rsi_wilder_first_value_matches_canonical() -> None:
    out = compute_rsi(_WILDER_CLOSES, period=14)
    first = out[14]
    assert first is not None
    # Canonical Wilder value ≈ 70.53. Allow 0.5 tolerance for rounding.
    assert math.isclose(first, 70.53, abs_tol=0.5)


def test_compute_rsi_subsequent_values_are_in_range() -> None:
    out = compute_rsi(_WILDER_CLOSES, period=14)
    for value in out[14:]:
        assert value is not None
        assert 0.0 <= value <= 100.0


def test_compute_rsi_too_few_closes_returns_all_none() -> None:
    closes = [100.0, 101.0, 102.0]
    out = compute_rsi(closes, period=14)
    assert out == [None, None, None]


def test_compute_rsi_only_gains_returns_100() -> None:
    closes = [float(i) for i in range(1, 30)]  # strictly monotone up
    out = compute_rsi(closes, period=14)
    assert out[14] == 100.0
    assert out[-1] == 100.0


def test_compute_rsi_only_losses_returns_0() -> None:
    closes = [float(i) for i in range(30, 1, -1)]  # strictly monotone down
    out = compute_rsi(closes, period=14)
    assert out[14] == 0.0
    assert out[-1] == 0.0


def test_compute_rsi_flat_returns_50() -> None:
    closes = [100.0] * 20
    out = compute_rsi(closes, period=14)
    # No gains, no losses → avg_loss=0 and avg_gain=0 → fall back to 50.
    assert out[14] == 50.0


def test_compute_rsi_invalid_period_raises() -> None:
    with pytest.raises(ValueError):
        compute_rsi([1.0, 2.0, 3.0], period=0)


def test_default_period_is_14() -> None:
    assert RSI_DEFAULT_PERIOD == 14
