"""Tests for the Source Reputation Engine.

Behaviour under test (not implementation details):
  * the exact operator-specified weighting,
  * the four usage-gate bands and their boundaries,
  * the non-negotiable safety invariant (no source triggers execution alone),
  * conservative handling of missing evidence (cold-start → middle band),
  * that negative axes (conflict / manipulation) actually pull the score down.
"""

from __future__ import annotations

import pytest

from app.observability.source_reputation import (
    MAX_REPUTATION,
    WEIGHTS,
    SourceReputationInputs,
    build_source_reputation_report,
    classify_usage_tier,
    score_source_reputation,
)


def test_max_reputation_is_sum_of_positive_weights() -> None:
    # Positive weights: 0.22+0.15+0.14+0.12+0.12+0.10 = 0.85.
    assert MAX_REPUTATION == pytest.approx(0.85)


def test_exact_weighted_score_all_dimensions_provided() -> None:
    inp = SourceReputationInputs(
        source_id="src.example",
        historical_accuracy=0.8,
        timeliness=0.7,
        originality=0.6,
        independence=0.5,
        domain_relevance=0.9,
        realized_signal_quality=0.4,
        conflict_rate=0.2,
        manipulation_probability=0.1,
    )
    expected = (
        0.22 * 0.8
        + 0.15 * 0.7
        + 0.14 * 0.6
        + 0.12 * 0.5
        + 0.12 * 0.9
        + 0.10 * 0.4
        - 0.08 * 0.2
        - 0.07 * 0.1
    )
    score = score_source_reputation(inp)
    assert score.raw_score == pytest.approx(expected)
    assert score.source_reputation == pytest.approx(max(0.0, min(1.0, expected)))
    assert score.data_completeness == pytest.approx(1.0)
    assert score.low_confidence is False


def test_weights_match_operator_spec() -> None:
    assert WEIGHTS == {
        "historical_accuracy": 0.22,
        "timeliness": 0.15,
        "originality": 0.14,
        "independence": 0.12,
        "domain_relevance": 0.12,
        "realized_signal_quality": 0.10,
        "conflict_rate": -0.08,
        "manipulation_probability": -0.07,
    }


@pytest.mark.parametrize(
    ("reputation", "expected_tier"),
    [
        (0.0, "research_only"),
        (0.29, "research_only"),
        (0.30, "supporting_evidence"),  # boundary → higher band
        (0.59, "supporting_evidence"),
        (0.60, "signal_support"),  # boundary → higher band
        (0.79, "signal_support"),
        (0.80, "high_trust_support"),  # boundary → higher band
        (0.85, "high_trust_support"),
    ],
)
def test_usage_gate_bands(reputation: float, expected_tier: str) -> None:
    assert classify_usage_tier(reputation) == expected_tier


def test_safety_invariant_no_source_triggers_execution_alone() -> None:
    """Even a near-perfect source must never authorise solo execution."""
    perfect = SourceReputationInputs(
        source_id="src.perfect",
        historical_accuracy=1.0,
        timeliness=1.0,
        originality=1.0,
        independence=1.0,
        domain_relevance=1.0,
        realized_signal_quality=1.0,
        conflict_rate=0.0,
        manipulation_probability=0.0,
        sample_size=500,
    )
    score = score_source_reputation(perfect)
    assert score.usage_tier == "high_trust_support"
    assert score.can_trigger_execution_alone is False
    assert score.max_role == "support"


def test_safety_invariant_holds_for_every_input() -> None:
    for hist in (0.0, 0.5, 1.0):
        for manip in (0.0, 0.5, 1.0):
            score = score_source_reputation(
                SourceReputationInputs(
                    source_id="s",
                    historical_accuracy=hist,
                    manipulation_probability=manip,
                )
            )
            assert score.can_trigger_execution_alone is False
            assert score.max_role == "support"


def test_missing_evidence_is_conservative_middle_band() -> None:
    """A cold-start source with no evidence lands in supporting-only, flagged."""
    score = score_source_reputation(SourceReputationInputs(source_id="src.cold"))
    # All positive dims default 0.5, negatives 0.0 → 0.5 * 0.85 = 0.425.
    assert score.source_reputation == pytest.approx(0.425)
    assert score.usage_tier == "supporting_evidence"
    assert score.low_confidence is True
    assert score.data_completeness == pytest.approx(0.0)
    # Cannot reach signal-support or high-trust without real evidence.
    assert score.usage_tier not in ("signal_support", "high_trust_support")


def test_manipulation_and_conflict_pull_score_down() -> None:
    clean = score_source_reputation(
        SourceReputationInputs(
            source_id="s",
            historical_accuracy=0.7,
            timeliness=0.7,
            originality=0.7,
            independence=0.7,
            domain_relevance=0.7,
            realized_signal_quality=0.7,
            conflict_rate=0.0,
            manipulation_probability=0.0,
        )
    )
    dirty = score_source_reputation(
        SourceReputationInputs(
            source_id="s",
            historical_accuracy=0.7,
            timeliness=0.7,
            originality=0.7,
            independence=0.7,
            domain_relevance=0.7,
            realized_signal_quality=0.7,
            conflict_rate=1.0,
            manipulation_probability=1.0,
        )
    )
    assert dirty.source_reputation < clean.source_reputation
    assert clean.source_reputation - dirty.source_reputation == pytest.approx(0.15)


def test_tracked_dimensions_do_not_affect_score() -> None:
    base = SourceReputationInputs(source_id="s", historical_accuracy=0.6)
    with_tracked = SourceReputationInputs(
        source_id="s",
        historical_accuracy=0.6,
        correction_history=0.9,
        bot_probability=0.8,
    )
    a = score_source_reputation(base)
    b = score_source_reputation(with_tracked)
    assert a.raw_score == pytest.approx(b.raw_score)
    assert b.tracked["correction_history"] == pytest.approx(0.9)
    assert b.tracked["bot_probability"] == pytest.approx(0.8)


def test_out_of_range_inputs_are_clamped() -> None:
    score = score_source_reputation(
        SourceReputationInputs(
            source_id="s",
            historical_accuracy=5.0,  # clamps to 1.0
            manipulation_probability=-3.0,  # clamps to 0.0
        )
    )
    assert score.dimensions["historical_accuracy"] == pytest.approx(1.0)
    assert score.dimensions["manipulation_probability"] == pytest.approx(0.0)


def test_report_is_sorted_and_serialisable() -> None:
    report = build_source_reputation_report(
        [
            SourceReputationInputs(source_id="low", historical_accuracy=0.1),
            SourceReputationInputs(source_id="high", historical_accuracy=0.9),
        ]
    )
    assert report["report_type"] == "source_reputation"
    assert report["invariant"] == "no_source_triggers_execution_alone"
    sources = report["sources"]
    assert isinstance(sources, list)
    # Sorted descending by reputation → "high" first.
    assert sources[0]["source_id"] == "high"
    assert sources[1]["source_id"] == "low"
