"""Unit tests for the unlock beta-neutral validation primitives.

The validation itself is a network research script; these cover its two pure,
correctness-critical helpers: de-overlapping to independent holds and the realized
short funding carry over a hold window.
"""

from __future__ import annotations

import pytest
from scripts.unlock_validation import _funding_carry_bps, select_independent


def test_select_independent_enforces_spacing_and_is_greedy() -> None:
    # bars at 0,1,2,...,10,200,201 with min_spacing 168 -> keep 0, then 200 (next >=168).
    indices = [0, 1, 2, 10, 167, 168, 200, 201, 400]
    keep = select_independent(indices, 168)
    assert keep == [0, 168, 400]
    # spacing invariant holds for the chosen set.
    assert all(b - a >= 168 for a, b in zip(keep, keep[1:], strict=False))


def test_select_independent_edge_cases() -> None:
    assert select_independent([], 168) == []
    assert select_independent([5], 168) == [5]
    # exactly at the boundary counts as independent.
    assert select_independent([0, 168], 168) == [0, 168]
    assert select_independent([0, 167], 168) == [0]


def test_funding_carry_is_open_exclusive_upper_inclusive_and_scaled() -> None:
    # settlements (ms, rate); hold (1000, 1000+horizon]. open-exclusive, upper-inclusive.
    funding = [(1000, 0.0001), (1500, 0.0002), (3000, -0.0003), (5000, 0.01)]
    # horizon covering (1000, 3000]: excludes 1000 (==open), includes 1500 & 3000.
    carry = _funding_carry_bps(funding, open_ms=1000, horizon_ms=2000)
    # (0.0002 + -0.0003) * 1e4 = -1.0 bps (a short PAID funding here).
    assert carry == pytest.approx(-1.0)


def test_funding_carry_empty_or_out_of_window_is_zero() -> None:
    assert _funding_carry_bps([], 0, 1000) == 0.0
    assert _funding_carry_bps([(10_000, 0.5)], open_ms=0, horizon_ms=1000) == 0.0
