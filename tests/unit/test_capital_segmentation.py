"""Capital segmentation accounting (ADR 0013, shadow-only, inert).

Generalises the Lightning treasury 3-account split to the 4 capital buckets
(operating/reserve/long_term/experiment). Pure, read-only: no capital is moved,
allocation/transfer is gated at the call site (HOTP + edge-validation-gate),
never in these functions.
"""

from __future__ import annotations

import pytest

from app.capital.segmentation import (
    BUCKETS,
    compute_segmentation_snapshot,
    is_allowed_transition,
)


def test_sums_total_and_shares() -> None:
    snap = compute_segmentation_snapshot(
        {"operating": 6000.0, "reserve": 3000.0, "long_term": 1000.0}
    )
    assert snap["total"] == pytest.approx(10000.0)
    assert snap["by_bucket"]["operating"] == pytest.approx(6000.0)
    assert snap["shares"]["operating"] == pytest.approx(0.6)
    assert snap["shares"]["reserve"] == pytest.approx(0.3)


def test_missing_buckets_zero_filled() -> None:
    snap = compute_segmentation_snapshot({"operating": 100.0})
    assert set(snap["by_bucket"]) == set(BUCKETS)
    assert snap["by_bucket"]["experiment"] == pytest.approx(0.0)


def test_rejects_unknown_bucket() -> None:
    with pytest.raises(ValueError):
        compute_segmentation_snapshot({"gambling": 1.0})


def test_rejects_negative_balance() -> None:
    with pytest.raises(ValueError):
        compute_segmentation_snapshot({"reserve": -1.0})


def test_empty_is_zero_no_div_by_zero() -> None:
    snap = compute_segmentation_snapshot({})
    assert snap["total"] == pytest.approx(0.0)
    assert all(v == pytest.approx(0.0) for v in snap["shares"].values())


def test_caveat_flags_shadow_and_gated() -> None:
    snap = compute_segmentation_snapshot({"operating": 1.0})
    caveat = snap["caveat"].lower()
    assert "shadow" in caveat
    assert "gate" in caveat  # allocation/transfer is gated at the call site


def test_transition_whitelist_reserve_never_reinvested() -> None:
    # legal promotions
    assert is_allowed_transition("operating", "reserve") is True
    assert is_allowed_transition("reserve", "long_term") is True
    assert is_allowed_transition("operating", "experiment") is True
    # reserve is OUT of the risk loop — never back into operating/trading
    assert is_allowed_transition("reserve", "operating") is False
    assert is_allowed_transition("long_term", "operating") is False
    # unknown buckets never transition
    assert is_allowed_transition("operating", "gambling") is False
