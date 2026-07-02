"""Unit tests for the L2 on-chain evidence factory + provider wiring (Sprint 2).

B-003 is the crux: L2 must be DIRECTION-AGNOSTIC. Unlike funding (which hardcodes
contrarian via ``signal_is_long``), ``build_l2_onchain_evidence`` has NO signal
direction input — the magnitude is the on-chain extremity and the direction is
supplied by the caller (``direction_aligned``, learned from evaluation). v1 always
passes ``direction_aligned=0`` (undetermined) → zero contribution (shadow-only).
"""

from __future__ import annotations

from app.signals.bayesian_confidence import (
    EvidenceKind,
    _calibrate,
    build_l2_onchain_evidence,
)


def test_factory_kind_and_extremity_magnitude() -> None:
    # Extreme percentiles (far from the 0.5 median) → high magnitude.
    extreme = build_l2_onchain_evidence(fee_percentile=1.0, mempool_percentile=1.0)
    assert extreme.kind == EvidenceKind.L2_ONCHAIN
    assert extreme.value > 0.9
    # Median percentiles → ~zero magnitude (nothing notable on-chain).
    median = build_l2_onchain_evidence(fee_percentile=0.5, mempool_percentile=0.5)
    assert median.value == 0.0


def test_factory_is_direction_agnostic_default_zero() -> None:
    ev = build_l2_onchain_evidence(fee_percentile=0.9, mempool_percentile=0.9)
    # v1 default: undetermined direction → zero contribution regardless of magnitude.
    assert ev.direction_aligned == 0
    assert _calibrate(EvidenceKind.L2_ONCHAIN, ev.value, ev.direction_aligned) == 0.0


def test_factory_direction_passed_through_not_derived_from_signal() -> None:
    # The factory takes NO signal_is_long — direction is supplied externally and
    # passed through unchanged (data-driven, learned). Magnitude is identical for
    # +1 / -1; only the sign of the contribution flips (symmetric, not inverted).
    pos = build_l2_onchain_evidence(fee_percentile=0.9, mempool_percentile=0.9, direction_aligned=1)
    neg = build_l2_onchain_evidence(
        fee_percentile=0.9, mempool_percentile=0.9, direction_aligned=-1
    )
    assert pos.value == neg.value
    assert pos.direction_aligned == 1
    assert neg.direction_aligned == -1
    c_pos = _calibrate(EvidenceKind.L2_ONCHAIN, pos.value, pos.direction_aligned)
    c_neg = _calibrate(EvidenceKind.L2_ONCHAIN, neg.value, neg.direction_aligned)
    assert c_pos == -c_neg and c_pos > 0


def test_factory_none_percentiles_zero_magnitude() -> None:
    ev = build_l2_onchain_evidence(fee_percentile=None, mempool_percentile=None)
    assert ev.value == 0.0  # no window → nothing to say, not a fabricated signal
