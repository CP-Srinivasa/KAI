"""oi_change_zscore — change-distribution z-score (Goal V5 Phase 2)."""

from __future__ import annotations

import math

import pytest

from app.market_data.oi_zscore import oi_change_zscore


def test_known_series_yields_expected_zscore() -> None:
    # OI series oldest→newest. Changes: +1,+1,+1,+1,+6.
    # mean(changes)=2.0, var=pop over [1,1,1,1,6] = mean of squared dev:
    #   devs: -1,-1,-1,-1,4 → squares 1,1,1,1,16 → sum 20 /5 = 4 → std=2.0
    # latest change = 6 → z = (6-2)/2 = 2.0
    series = [10.0, 11.0, 12.0, 13.0, 14.0, 20.0]
    z = oi_change_zscore(series)
    assert z == pytest.approx(2.0)


def test_negative_latest_change_is_negative_z() -> None:
    # Changes: +1,+1,+1,+1,-9 → mean=-1.0
    # devs: 2,2,2,2,-8 → sq 4,4,4,4,64 → sum 80/5=16 → std=4
    # latest=-9 → z=(-9-(-1))/4 = -2.0
    series = [10.0, 11.0, 12.0, 13.0, 14.0, 5.0]
    z = oi_change_zscore(series)
    assert z == pytest.approx(-2.0)


def test_flat_series_zero_variance_returns_zero() -> None:
    assert oi_change_zscore([5.0, 5.0, 5.0, 5.0]) == 0.0


def test_perfectly_linear_series_zero_variance_returns_zero() -> None:
    # constant change → zero variance in changes → 0.0 (no surprise)
    assert oi_change_zscore([1.0, 2.0, 3.0, 4.0, 5.0]) == 0.0


def test_too_few_points_returns_zero() -> None:
    assert oi_change_zscore([]) == 0.0
    assert oi_change_zscore([1.0]) == 0.0
    assert oi_change_zscore([1.0, 2.0]) == 0.0  # only 1 change → < 2 changes


def test_non_finite_inputs_dropped() -> None:
    # nan dropped → series [10,11,13,14,20]; changes [1,2,1,6], mean=2.5,
    # devs [-1.5,-0.5,-1.5,3.5] sq [2.25,0.25,2.25,12.25] sum 17/4=4.25,
    # std=2.0616, latest change 6 → z=(6-2.5)/2.0616≈1.6977
    z = oi_change_zscore([10.0, 11.0, float("nan"), 13.0, 14.0, 20.0])
    assert math.isfinite(z)
    assert z == pytest.approx(1.6977, abs=1e-3)
