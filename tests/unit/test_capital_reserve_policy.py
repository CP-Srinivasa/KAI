"""Reserve/profit-split recommendation (ADR 0013, shadow-only, inert).

Pure recommendation: "what SHOULD move to reserve on this realized gain" — never
executes. Reserve is capped at its target; the overflow rolls to long-term hold.
Actual movement is gated at the call site (HOTP + edge-validation-gate).
"""

from __future__ import annotations

import pytest

from app.capital.reserve_policy import compute_reserve_recommendation


def test_basic_split_below_target() -> None:
    rec = compute_reserve_recommendation(
        1000.0, current_reserve_usd=0.0, profit_split_pct=0.5, reserve_target_usd=10000.0
    )
    assert rec["to_reserve_usd"] == pytest.approx(500.0)
    assert rec["to_long_term_usd"] == pytest.approx(0.0)
    assert rec["keep_operating_usd"] == pytest.approx(500.0)
    assert rec["executes"] is False


def test_reserve_capped_overflow_rolls_to_long_term() -> None:
    rec = compute_reserve_recommendation(
        1000.0, current_reserve_usd=100.0, profit_split_pct=1.0, reserve_target_usd=300.0
    )
    # room in reserve = 300 - 100 = 200; rest of the 1000 split rolls to long-term
    assert rec["to_reserve_usd"] == pytest.approx(200.0)
    assert rec["to_long_term_usd"] == pytest.approx(800.0)


def test_no_or_negative_gain_yields_zero() -> None:
    for gain in (0.0, -250.0):
        rec = compute_reserve_recommendation(
            gain, current_reserve_usd=0.0, profit_split_pct=0.5, reserve_target_usd=10000.0
        )
        assert rec["to_reserve_usd"] == pytest.approx(0.0)
        assert rec["to_long_term_usd"] == pytest.approx(0.0)


def test_never_executes() -> None:
    rec = compute_reserve_recommendation(
        5000.0, current_reserve_usd=0.0, profit_split_pct=0.2, reserve_target_usd=1000.0
    )
    assert rec["executes"] is False


def test_rejects_bad_split_pct() -> None:
    with pytest.raises(ValueError):
        compute_reserve_recommendation(
            100.0, current_reserve_usd=0.0, profit_split_pct=1.5, reserve_target_usd=10.0
        )
