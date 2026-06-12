"""SENTIMENT_OVERHEAT-Evidence (HYPE-S1) — Factory-Semantik + Engine-Wirkung.

Kernrisiko: eine falsch gepolte Evidence würde Hype als BESTÄTIGUNG statt als
Warnung verrechnen. Geprüft wird deshalb die Richtungs-Semantik der Factory
(LONG → contra; SHORT nur in symmetric mode pro) und dass die Evidence in der
Bayes-Engine den Posterior eines Long-Signals tatsächlich SENKT.
"""

from __future__ import annotations

import pytest

from app.signals.bayesian_confidence import (
    BayesianConfidenceEngine,
    ContributionEffect,
    EvidenceKind,
    build_sentiment_overheat_evidence,
)

# ── Factory-Richtungs-Semantik ────────────────────────────────────────────────


def test_long_signal_overheat_is_contra() -> None:
    ev = build_sentiment_overheat_evidence(hype_score=0.8, signal_is_long=True)
    assert ev.kind == EvidenceKind.SENTIMENT_OVERHEAT
    assert ev.direction_aligned == -1
    assert ev.value == 0.8


def test_short_signal_dampen_only_sets_no_direction() -> None:
    ev = build_sentiment_overheat_evidence(hype_score=0.8, signal_is_long=False)
    assert ev.direction_aligned == 0  # Engine verwirft → Hype begründet keine Shorts


def test_short_signal_symmetric_mode_is_pro() -> None:
    ev = build_sentiment_overheat_evidence(hype_score=0.8, signal_is_long=False, dampen_only=False)
    assert ev.direction_aligned == 1


def test_zero_score_sets_no_direction_regardless_of_side() -> None:
    assert (
        build_sentiment_overheat_evidence(hype_score=0.0, signal_is_long=True).direction_aligned
        == 0
    )
    assert (
        build_sentiment_overheat_evidence(
            hype_score=0.0, signal_is_long=False, dampen_only=False
        ).direction_aligned
        == 0
    )


def test_score_is_clamped_to_unit_interval() -> None:
    assert build_sentiment_overheat_evidence(hype_score=1.7, signal_is_long=True).value == 1.0
    assert build_sentiment_overheat_evidence(hype_score=-0.3, signal_is_long=True).value == 0.0


# ── Engine-Wirkung: Überhitzung senkt den Long-Posterior ──────────────────────


def test_overheat_evidence_decreases_long_posterior() -> None:
    engine = BayesianConfidenceEngine()
    ev = build_sentiment_overheat_evidence(hype_score=0.9, signal_is_long=True, source_trust=0.5)
    report = engine.evaluate([ev], prior_probability=0.65)
    assert report.posterior_probability < report.prior_probability
    assert len(report.decreased) == 1
    assert report.decreased[0].kind == EvidenceKind.SENTIMENT_OVERHEAT


def test_dampen_only_short_contribution_is_discarded_not_counted() -> None:
    engine = BayesianConfidenceEngine()
    ev = build_sentiment_overheat_evidence(hype_score=0.9, signal_is_long=False)
    report = engine.evaluate([ev], prior_probability=0.65)
    assert report.posterior_probability == pytest.approx(report.prior_probability)
    assert len(report.discarded) == 1
    assert report.discarded[0].effect == ContributionEffect.DISCARDED


def test_overheat_dampens_less_than_equally_trusted_onchain_evidence() -> None:
    # Kategorialer Stärke-Modulator: Hype (0.6) ist bewusst schwächer als
    # On-Chain (1.2) — die Quelle ist verrauschter und darf nicht dominieren.
    from app.signals.bayesian_confidence import build_on_chain_evidence

    engine = BayesianConfidenceEngine()
    hype = build_sentiment_overheat_evidence(hype_score=1.0, signal_is_long=True)
    onchain_contra = build_on_chain_evidence(
        netflow_zscore=3.0, inflow_to_exchange=True, signal_is_long=True
    )
    p_hype = engine.evaluate([hype], prior_probability=0.65).posterior_probability
    p_onchain = engine.evaluate([onchain_contra], prior_probability=0.65).posterior_probability
    assert p_onchain < p_hype < 0.65
