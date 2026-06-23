"""Benjamini-Hochberg FDR-control tests."""

from __future__ import annotations

import pytest

from app.research.multiple_testing import benjamini_hochberg


def test_all_tiny_p_values_all_rejected() -> None:
    assert benjamini_hochberg([0.001, 0.002, 0.003], alpha=0.05) == [True, True, True]


def test_all_large_p_values_none_rejected() -> None:
    assert benjamini_hochberg([0.5, 0.9, 0.7], alpha=0.05) == [False, False, False]


def test_mixed_alignment_preserved() -> None:
    # sorted: 0.001(r1,thr0.0125 ok), 0.02(r2,thr0.025 ok), 0.7(no), 0.8(no)
    # -> reject the two smallest, mask aligned to input order.
    mask = benjamini_hochberg([0.001, 0.7, 0.02, 0.8], alpha=0.05)
    assert mask == [True, False, True, False]


def test_step_up_rejects_below_max_rank_even_if_self_fails() -> None:
    # p=[0.001, 0.04, 0.039], m=3, alpha=0.05.
    # thresholds: r1=0.0167, r2=0.0333, r3=0.05.
    # 0.04 at rank3 passes (<=0.05) -> max_rank=3 -> 0.039 rejected too,
    # despite 0.039 > its own rank-2 threshold (step-up property).
    assert benjamini_hochberg([0.001, 0.04, 0.039], alpha=0.05) == [True, True, True]


def test_empty_returns_empty() -> None:
    assert benjamini_hochberg([], alpha=0.05) == []


def test_invalid_alpha_raises() -> None:
    with pytest.raises(ValueError):
        benjamini_hochberg([0.1], alpha=0.0)
    with pytest.raises(ValueError):
        benjamini_hochberg([0.1], alpha=1.5)


def test_p_value_out_of_range_raises() -> None:
    with pytest.raises(ValueError):
        benjamini_hochberg([0.1, 1.2], alpha=0.05)
