"""EMA indicator tests.

Golden values are hand-computed: EMA seeded with the SMA of the first `period`
values, then recursive smoothing with alpha = 2/(period+1).
"""

from __future__ import annotations

import pytest

from app.analysis.indicators.ema import compute_ema


def test_compute_ema_warmup_and_seed_hand_computed() -> None:
    # values [1,2,3,4,5], period 3, alpha = 2/4 = 0.5.
    # seed (idx2) = mean(1,2,3) = 2.0
    # idx3 = 4*0.5 + 2.0*0.5 = 3.0
    # idx4 = 5*0.5 + 3.0*0.5 = 4.0
    out = compute_ema([1.0, 2.0, 3.0, 4.0, 5.0], period=3)
    assert out == [None, None, 2.0, 3.0, 4.0]


def test_compute_ema_flat_series_stays_flat() -> None:
    out = compute_ema([5.0, 5.0, 5.0, 5.0], period=2)
    assert out == [None, 5.0, 5.0, 5.0]


def test_compute_ema_length_aligned_to_input() -> None:
    values = [float(i) for i in range(20)]
    out = compute_ema(values, period=5)
    assert len(out) == len(values)


def test_compute_ema_too_few_values_returns_all_none() -> None:
    out = compute_ema([1.0, 2.0], period=5)
    assert out == [None, None]


def test_compute_ema_lags_a_rising_price() -> None:
    # On a strictly rising series the EMA trails the latest price.
    values = [float(i) for i in range(1, 30)]
    out = compute_ema(values, period=10)
    assert out[-1] is not None
    assert out[-1] < values[-1]


def test_compute_ema_invalid_period_raises() -> None:
    with pytest.raises(ValueError):
        compute_ema([1.0, 2.0, 3.0], period=0)
