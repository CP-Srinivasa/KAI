"""Bollinger z-score indicator tests.

z[i] = (close[i] - mean(window)) / sample_std(window), window ending at i.
Sample std (n-1), consistent with realized_volatility. Causal by construction.
"""

from __future__ import annotations

import math

import pytest

from app.analysis.indicators.bollinger import (
    BOLLINGER_DEFAULT_WINDOW,
    compute_bollinger_z,
)


def test_bollinger_z_hand_computed_value() -> None:
    # closes [1,2,3,4,5], window 5: mean=3, sample var=10/4=2.5, std=sqrt(2.5).
    # z[4] = (5-3)/sqrt(2.5) = 1.264911...
    out = compute_bollinger_z([1.0, 2.0, 3.0, 4.0, 5.0], window=5)
    assert out[:4] == [None, None, None, None]
    assert out[4] is not None
    assert math.isclose(out[4], 2.0 / math.sqrt(2.5), abs_tol=1e-9)


def test_bollinger_z_flat_window_is_zero() -> None:
    out = compute_bollinger_z([5.0, 5.0, 5.0, 5.0, 5.0], window=5)
    assert out[4] == 0.0


def test_bollinger_z_warmup_is_none() -> None:
    out = compute_bollinger_z([float(i) for i in range(10)], window=5)
    for i in range(4):
        assert out[i] is None
    assert out[4] is not None


def test_bollinger_z_too_few_closes_all_none() -> None:
    out = compute_bollinger_z([1.0, 2.0, 3.0], window=5)
    assert out == [None, None, None]


def test_bollinger_z_length_aligned_to_input() -> None:
    closes = [float(i) for i in range(30)]
    out = compute_bollinger_z(closes, window=20)
    assert len(out) == len(closes)


def test_bollinger_z_invalid_window_raises() -> None:
    with pytest.raises(ValueError):
        compute_bollinger_z([1.0, 2.0, 3.0], window=1)


def test_bollinger_default_window_is_20() -> None:
    assert BOLLINGER_DEFAULT_WINDOW == 20
