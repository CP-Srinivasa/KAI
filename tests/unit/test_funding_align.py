"""Causal funding-to-bar alignment tests.

The three real risks of mapping sparse 8h funding onto dense bars:
  1. CAUSALITY: a bar must never see a funding event settled AFTER it.
  2. Forward-fill: between settlements a bar carries the last settled rate.
  3. Z-score correctness + None semantics (warm-up / zero variance).
"""

from __future__ import annotations

import math
import statistics
from datetime import UTC, datetime

from app.analysis.features.funding_align import (
    FUNDING_Z_MIN_POINTS,
    FundingPoint,
    align_funding_to_bars,
)

_H = 3_600_000  # 1h in ms
_8H = 8 * _H


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def test_empty_funding_yields_all_none() -> None:
    bars = [_iso(i * _H) for i in range(5)]
    rate, rate_z = align_funding_to_bars(bars, [])
    assert rate == [None] * 5
    assert rate_z == [None] * 5


def test_forward_fill_between_settlements() -> None:
    # Funding settles at t=0 (0.0001) and t=8h (0.0003).
    funding = [FundingPoint(0, 0.0001), FundingPoint(_8H, 0.0003)]
    # Bars every hour from t=0..15h.
    bars = [_iso(i * _H) for i in range(16)]
    rate, _ = align_funding_to_bars(bars, funding)
    # Bars 0..7 (t=0..7h) see only the first settlement.
    for i in range(8):
        assert rate[i] == 0.0001
    # Bars 8..15 (t=8..15h) carry the second settlement forward.
    for i in range(8, 16):
        assert rate[i] == 0.0003


def test_bar_before_first_settlement_is_none() -> None:
    # First settlement at t=8h; bars at t=0..7h precede it → None.
    funding = [FundingPoint(_8H, 0.0002)]
    bars = [_iso(i * _H) for i in range(10)]
    rate, rate_z = align_funding_to_bars(bars, funding)
    for i in range(8):
        assert rate[i] is None
        assert rate_z[i] is None
    assert rate[8] == 0.0002
    assert rate[9] == 0.0002


def test_no_lookahead_settlement_after_bar_is_invisible() -> None:
    # A settlement exactly at the bar's timestamp IS visible (settled at open);
    # one strictly after is NOT. Bar at t=8h.
    bars = [_iso(_8H)]
    at_open = align_funding_to_bars(bars, [FundingPoint(_8H, 0.0005)])[0]
    after = align_funding_to_bars(bars, [FundingPoint(_8H + 1, 0.0005)])[0]
    assert at_open[0] == 0.0005  # settled at-or-before open → visible
    assert after[0] is None  # settled after the bar opened → look-ahead, hidden


def test_zscore_warmup_then_value() -> None:
    # A calm regime (~0.0001) followed by a clear funding spike (0.0008).
    rates = [0.0001, 0.00011, 0.00009, 0.0001, 0.00011, 0.0008]
    funding = [FundingPoint(i * _8H, r) for i, r in enumerate(rates)]
    # One bar per settlement, sampled right at each settlement time.
    bars = [_iso(i * _8H) for i in range(len(rates))]
    _, rate_z = align_funding_to_bars(bars, funding, z_window=24)
    # First (FUNDING_Z_MIN_POINTS - 1) bars: insufficient window → None.
    for i in range(FUNDING_Z_MIN_POINTS - 1):
        assert rate_z[i] is None
    # Last bar: population z of the spike over the full known window.
    last = len(rates) - 1
    expected = (rates[last] - statistics.fmean(rates)) / statistics.pstdev(rates)
    assert rate_z[last] is not None
    assert math.isclose(rate_z[last], expected, rel_tol=1e-9)
    assert rate_z[last] > 2.0  # the 0.0008 spike crosses the fade threshold


def test_zscore_zero_variance_is_none() -> None:
    # Constant funding regime → no dispersion → z undefined (not a fake 0).
    funding = [FundingPoint(i * _8H, 0.0001) for i in range(6)]
    bars = [_iso(i * _8H) for i in range(6)]
    _, rate_z = align_funding_to_bars(bars, funding)
    assert all(z is None for z in rate_z)


def test_unsorted_funding_is_handled() -> None:
    # Input order must not matter — the function sorts by settlement time.
    funding = [FundingPoint(_8H, 0.0003), FundingPoint(0, 0.0001)]
    bars = [_iso(0), _iso(_8H)]
    rate, _ = align_funding_to_bars(bars, funding)
    assert rate == [0.0001, 0.0003]


def test_unparseable_timestamp_degrades_to_none() -> None:
    # Synthetic non-ISO bar labels (as some unit fixtures use) → None, no raise.
    funding = [FundingPoint(0, 0.0001)]
    rate, rate_z = align_funding_to_bars(["bar-0", "bar-1"], funding)
    assert rate == [None, None]
    assert rate_z == [None, None]
