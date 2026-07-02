"""Net-bps summary statistics tests."""

from __future__ import annotations

import math

from app.research.stats import summarize_net_bps


def test_empty_sample_is_non_significant() -> None:
    s = summarize_net_bps([])
    assert s.n == 0
    assert s.p_value == 1.0


def test_single_sample_cannot_claim_significance() -> None:
    s = summarize_net_bps([500.0])
    assert s.n == 1
    assert s.mean_bps == 500.0
    assert s.p_value == 1.0  # conservative: no dispersion estimate


def test_constant_positive_sample_is_significant() -> None:
    s = summarize_net_bps([100.0] * 30)
    assert s.mean_bps == 100.0
    assert s.std_bps == 0.0
    assert s.hit_rate == 1.0
    assert s.p_value == 0.0


def test_constant_negative_sample_not_significant() -> None:
    s = summarize_net_bps([-100.0] * 30)
    assert s.p_value == 1.0
    assert s.hit_rate == 0.0


def test_symmetric_around_zero_is_p_half() -> None:
    sample = [100.0, -100.0] * 25  # mean exactly 0
    s = summarize_net_bps(sample)
    assert math.isclose(s.mean_bps, 0.0, abs_tol=1e-9)
    assert math.isclose(s.p_value, 0.5, abs_tol=1e-9)


def test_positive_mean_gives_p_below_half_negative_above() -> None:
    pos = summarize_net_bps([30.0, 20.0, 40.0, 35.0, 25.0, 30.0, 28.0, 32.0])
    neg = summarize_net_bps([-30.0, -20.0, -40.0, -35.0, -25.0, -30.0, -28.0, -32.0])
    assert pos.mean_bps > 0 and pos.p_value < 0.5
    assert neg.mean_bps < 0 and neg.p_value > 0.5


def test_hit_rate_counts_strictly_positive() -> None:
    s = summarize_net_bps([10.0, -10.0, 0.0, 20.0])
    assert s.hit_rate == 0.5  # two of four strictly > 0
