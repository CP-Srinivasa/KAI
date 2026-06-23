"""Forward-return labeling tests.

The label is the GROSS market move over a forward horizon, in basis points:
    fwd_bps[i] = 10000 * (close[i + horizon] / close[i] - 1)

This is intentionally FORWARD-looking (uses a future bar) — that is correct for
a supervised LABEL. The no-look-ahead rule applies to FEATURES, never to the
label. Costs are NOT baked in here (label = market truth; cost overlay is
applied at hypothesis-eval time via the CostModel). The last `horizon` rows
have no future bar and are therefore None.
"""

from __future__ import annotations

import math

import pytest

from app.analysis.features.forward_returns import compute_forward_return_bps


def test_simple_positive_return_in_bps() -> None:
    # +10% per step → 1000 bps.
    out = compute_forward_return_bps([100.0, 110.0, 121.0], horizon=1)
    assert out[0] is not None and math.isclose(out[0], 1000.0, abs_tol=1e-6)
    assert out[1] is not None and math.isclose(out[1], 1000.0, abs_tol=1e-6)
    assert out[2] is None  # no future bar


def test_multi_step_horizon_compounds() -> None:
    # horizon 2 over [100 -> 121] = +21% = 2100 bps.
    out = compute_forward_return_bps([100.0, 110.0, 121.0], horizon=2)
    assert out[0] is not None and math.isclose(out[0], 2100.0, abs_tol=1e-6)
    assert out[1] is None
    assert out[2] is None


def test_negative_return() -> None:
    out = compute_forward_return_bps([100.0, 90.0], horizon=1)
    assert out[0] is not None and math.isclose(out[0], -1000.0, abs_tol=1e-6)
    assert out[1] is None


def test_last_horizon_rows_are_none() -> None:
    closes = [float(i) for i in range(1, 11)]
    out = compute_forward_return_bps(closes, horizon=3)
    assert out[-3:] == [None, None, None]
    assert out[0] is not None


def test_non_positive_price_yields_none_at_affected_indices() -> None:
    # A zero/negative price makes the ratio undefined → None, without crashing.
    out = compute_forward_return_bps([100.0, 0.0, 120.0], horizon=1)
    assert out[0] is None  # close[1] == 0 → undefined
    assert out[1] is None  # close[1] == 0 base → undefined
    assert out[2] is None  # no future bar


def test_length_aligned_to_input() -> None:
    closes = [float(i) for i in range(1, 21)]
    out = compute_forward_return_bps(closes, horizon=5)
    assert len(out) == len(closes)


def test_empty_returns_empty() -> None:
    assert compute_forward_return_bps([], horizon=1) == []


def test_horizon_must_be_positive() -> None:
    with pytest.raises(ValueError):
        compute_forward_return_bps([1.0, 2.0, 3.0], horizon=0)
