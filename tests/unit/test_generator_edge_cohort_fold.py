"""Cohort-key-mismatch fix (2026-06-13): real_analysis fills + autonomous_generator
shadow IC/Brier must land in ONE generator profile.

Befund beim n=77-Zwischenstand: the IC/Brier side-channel is keyed
``autonomous_generator`` (shadow) while the executed paper fills are keyed
``real_analysis`` (audit, decoupled feeder B-002). They never joined, so the
generator profile showed IC/Brier with resolved_count=0 and the real_analysis
profile showed EV with no IC/Brier — regardless of sample size.
"""

from __future__ import annotations

from app.learning.calibration import OutcomePair
from app.observability.edge_report import ClosedTrade
from app.observability.generator_edge import (
    COHORT_AUTONOMOUS_GENERATOR,
    build_generator_edge_report,
    canonical_generator_cohort,
)


def _trade(source: str, *, ts: str) -> ClosedTrade:
    return ClosedTrade(
        symbol="BTC/USDT",
        position_side="long",
        entry_price=1.0,
        exit_price=1.05,
        quantity=1.0,
        reason="tp",
        trade_pnl_usd=0.05,
        fee_usd=0.0,
        timestamp_utc=ts,
        regime="bull",
        signal_source=source,
    )


# ── pure taxonomy function ───────────────────────────────────────────────────


def test_canonical_folds_real_analysis_onto_generator() -> None:
    assert canonical_generator_cohort("real_analysis") == COHORT_AUTONOMOUS_GENERATOR
    assert canonical_generator_cohort("autonomous_generator") == COHORT_AUTONOMOUS_GENERATOR


def test_canonical_leaves_other_sources_distinct() -> None:
    # canary and legacy/unknown must NOT be folded in (would re-poison edge)
    assert canonical_generator_cohort("canary_probe") == "canary_probe"
    assert canonical_generator_cohort("tv_promoted") == "tv_promoted"
    assert canonical_generator_cohort("") == "unknown"
    assert canonical_generator_cohort(None) == "unknown"


def test_canonical_is_idempotent() -> None:
    once = canonical_generator_cohort("real_analysis")
    assert canonical_generator_cohort(once) == once


# ── end-to-end: EV (audit) + IC/Brier (shadow) join in one profile ──────────


def test_real_analysis_fills_join_shadow_ic_brier() -> None:
    # executed fills tagged real_analysis (the audit reality)
    trades = [
        _trade("real_analysis", ts=f"2026-06-1{1 + i % 3}T{i % 24:02d}:00:00+00:00")
        for i in range(6)
    ]
    # side-channels keyed autonomous_generator (the shadow reality);
    # >= min_sample (20) aligned points so the IC is actually computed
    aligned_1m = [(0.01 * i, 0.02 * i) for i in range(-12, 12)]  # positively correlated
    ic_by_cohort = {"autonomous_generator": {"1m": aligned_1m}}
    pairs_by_cohort = {
        "autonomous_generator": [
            OutcomePair(decision_id=f"d{i}", predicted_probability=0.7, actual_outcome=i % 2)
            for i in range(8)
        ]
    }

    report = build_generator_edge_report(
        trades,
        cohort_type="generator",
        ic_aligned_by_cohort=ic_by_cohort,
        outcome_pairs_by_cohort=pairs_by_cohort,
    )

    by_key = {p.cohort_key: p for p in report.profiles}
    # exactly ONE generator cohort, not two split halves
    assert COHORT_AUTONOMOUS_GENERATOR in by_key
    assert "real_analysis" not in by_key

    prof = by_key[COHORT_AUTONOMOUS_GENERATOR]
    # EV side (from the real_analysis fills) is now populated …
    assert prof.resolved_count == 6
    assert prof.expected_value_after_costs_bps is not None
    # … AND the IC/Brier side (from the autonomous_generator shadow) too
    assert prof.ic_by_horizon["1m"] is not None
    assert prof.brier_score is not None


def test_canary_fills_do_not_fold_into_generator() -> None:
    trades = [
        _trade("real_analysis", ts="2026-06-11T01:00:00+00:00"),
        _trade("canary_probe", ts="2026-06-11T02:00:00+00:00"),
    ]
    report = build_generator_edge_report(trades, cohort_type="generator")
    by_key = {p.cohort_key: p for p in report.profiles}
    assert COHORT_AUTONOMOUS_GENERATOR in by_key
    assert "canary_probe" in by_key
    assert by_key[COHORT_AUTONOMOUS_GENERATOR].resolved_count == 1
    assert by_key["canary_probe"].resolved_count == 1
