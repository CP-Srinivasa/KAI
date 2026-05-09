"""Unit tests for the Bayesian Signal Confidence Engine.

Pflichtfokus (KAI Testing-Regeln): Verhalten, nicht Implementierung.

Abdeckung:
  - Prior-Mix (kein hit_rate / wenig Samples / voll)
  - Posterior-Update (pro / contra / leer / konfliktär)
  - Discard-Pfade (direction_aligned=0, source_trust=0)
  - Decay (Stale-Evidence)
  - Semantik-Helfer (Funding-Inversion, Liquidations, OI, Regime)
  - Pydantic-Validierung (value-Range, naive datetime)
  - Determinismus
  - Erklär-Output (Restunsicherheits-Treiber)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.signals.bayesian_confidence import (
    PRIOR_BASE,
    BayesianConfidenceEngine,
    ContributionEffect,
    Evidence,
    EvidenceKind,
    build_default_engine,
    build_funding_rate_evidence,
    build_historical_hit_rate_evidence,
    build_liquidations_evidence,
    build_market_regime_evidence,
    build_news_evidence,
    build_on_chain_evidence,
    build_open_interest_evidence,
    build_volume_evidence,
)

# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> BayesianConfidenceEngine:
    return build_default_engine()


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)


# ─── Prior ───────────────────────────────────────────────────────────────────


class TestComputePrior:
    def test_no_hit_rate_falls_back_to_base(self, engine: BayesianConfidenceEngine) -> None:
        assert engine.compute_prior() == pytest.approx(PRIOR_BASE)

    def test_zero_observations_falls_back_to_base(self, engine: BayesianConfidenceEngine) -> None:
        assert engine.compute_prior(historical_hit_rate=0.95, n_observations=0) == pytest.approx(
            PRIOR_BASE
        )

    def test_high_hit_rate_with_full_sample_dominates(
        self, engine: BayesianConfidenceEngine
    ) -> None:
        prior = engine.compute_prior(historical_hit_rate=0.8, n_observations=100, source_trust=1.0)
        # weight = min(1, 100/30) * 1 = 1 → prior = 1*0.8 + 0*0.5 = 0.8
        assert prior == pytest.approx(0.8, abs=1e-6)

    def test_small_sample_is_diluted_toward_base(self, engine: BayesianConfidenceEngine) -> None:
        prior = engine.compute_prior(historical_hit_rate=1.0, n_observations=3, source_trust=1.0)
        # weight = 3/30 = 0.1 → prior = 0.1*1.0 + 0.9*0.5 = 0.55
        assert prior == pytest.approx(0.55, abs=1e-6)

    def test_low_source_trust_dampens_hit_rate(self, engine: BayesianConfidenceEngine) -> None:
        prior_high = engine.compute_prior(
            historical_hit_rate=0.9, n_observations=100, source_trust=1.0
        )
        prior_low = engine.compute_prior(
            historical_hit_rate=0.9, n_observations=100, source_trust=0.1
        )
        assert prior_high > prior_low
        # Low-trust prior pulled toward base
        assert abs(prior_low - PRIOR_BASE) < abs(prior_high - PRIOR_BASE)

    def test_prior_clamped_inside_open_interval(self, engine: BayesianConfidenceEngine) -> None:
        p = engine.compute_prior(historical_hit_rate=1.0, n_observations=10_000)
        assert 0.0 < p < 1.0

    def test_invalid_prior_base_rejected(self) -> None:
        with pytest.raises(ValueError):
            BayesianConfidenceEngine(prior_base=0.0)
        with pytest.raises(ValueError):
            BayesianConfidenceEngine(prior_base=1.0)


# ─── Posterior / Update ──────────────────────────────────────────────────────


class TestEvaluate:
    def test_no_evidence_yields_posterior_equal_to_prior(
        self, engine: BayesianConfidenceEngine, now: datetime
    ) -> None:
        report = engine.evaluate([], prior_probability=0.5, now=now)
        assert report.posterior_probability == pytest.approx(0.5, abs=1e-6)
        assert report.evidence_weight == pytest.approx(0.0)
        assert report.uncertainty_score == pytest.approx(1.0)
        assert report.confidence_score == pytest.approx(0.0)
        assert "Keine Evidenz" in report.residual_uncertainty_drivers[0]

    def test_strong_supporting_evidence_raises_posterior(
        self, engine: BayesianConfidenceEngine, now: datetime
    ) -> None:
        evidences = [
            build_news_evidence(
                relevance=0.9,
                sentiment_aligned_with_signal=True,
                source_trust=1.0,
                observed_at=now,
            ),
            build_volume_evidence(
                volume_zscore=2.0,
                price_move_aligned_with_signal=True,
                source_trust=1.0,
                observed_at=now,
            ),
        ]
        report = engine.evaluate(evidences, prior_probability=0.5, now=now)
        assert report.posterior_probability > 0.7
        assert report.confidence_score > 0.3
        assert report.uncertainty_score < 0.7
        assert len(report.increased) == 2
        assert not report.decreased

    def test_strong_contra_evidence_lowers_posterior(
        self, engine: BayesianConfidenceEngine, now: datetime
    ) -> None:
        evidences = [
            build_news_evidence(
                relevance=0.9,
                sentiment_aligned_with_signal=False,
                source_trust=1.0,
                observed_at=now,
            ),
        ]
        report = engine.evaluate(evidences, prior_probability=0.5, now=now)
        assert report.posterior_probability < 0.4
        assert len(report.decreased) == 1
        assert not report.increased

    def test_conflicting_evidences_keep_posterior_near_prior(
        self, engine: BayesianConfidenceEngine, now: datetime
    ) -> None:
        pro = build_news_evidence(
            relevance=0.9, sentiment_aligned_with_signal=True, observed_at=now
        )
        contra = build_news_evidence(
            relevance=0.9, sentiment_aligned_with_signal=False, observed_at=now
        )
        report = engine.evaluate([pro, contra], prior_probability=0.5, now=now)
        assert report.posterior_probability == pytest.approx(0.5, abs=1e-6)
        # Hohe evidence_weight, aber agreement ≈ 0 → confidence muss niedrig sein.
        assert report.evidence_weight > 0
        assert report.agreement < 0.1
        assert report.confidence_score < 0.1
        assert any("Konflikt" in d for d in report.residual_uncertainty_drivers)

    def test_posterior_in_open_unit_interval(
        self, engine: BayesianConfidenceEngine, now: datetime
    ) -> None:
        # Extrem-Stack: lauter +1-Evidence, soll dennoch < 1.0 bleiben.
        evidences = [
            Evidence(kind=EvidenceKind.NEWS_RELEVANCE, value=1.0, direction_aligned=1),
            Evidence(kind=EvidenceKind.ON_CHAIN, value=1.0, direction_aligned=1),
            Evidence(kind=EvidenceKind.VOLUME_REACTION, value=1.0, direction_aligned=1),
            Evidence(kind=EvidenceKind.OPEN_INTEREST, value=1.0, direction_aligned=1),
            Evidence(kind=EvidenceKind.LIQUIDATIONS, value=1.0, direction_aligned=1),
        ]
        report = engine.evaluate(evidences, prior_probability=0.5, now=now)
        assert 0.0 < report.posterior_probability < 1.0
        assert 0.0 <= report.confidence_score <= 1.0
        assert 0.0 <= report.uncertainty_score <= 1.0


# ─── Discard / Modulator ─────────────────────────────────────────────────────


class TestDiscardPaths:
    def test_neutral_direction_is_discarded(
        self, engine: BayesianConfidenceEngine, now: datetime
    ) -> None:
        ev = Evidence(kind=EvidenceKind.NEWS_RELEVANCE, value=0.9, direction_aligned=0)
        report = engine.evaluate([ev], prior_probability=0.5, now=now)
        assert report.posterior_probability == pytest.approx(0.5)
        assert len(report.discarded) == 1
        assert report.discarded[0].effect == ContributionEffect.DISCARDED

    def test_zero_source_trust_is_discarded(
        self, engine: BayesianConfidenceEngine, now: datetime
    ) -> None:
        ev = Evidence(
            kind=EvidenceKind.NEWS_RELEVANCE,
            value=0.9,
            direction_aligned=1,
            source_trust=0.0,
        )
        report = engine.evaluate([ev], prior_probability=0.5, now=now)
        assert report.posterior_probability == pytest.approx(0.5)
        assert len(report.discarded) == 1

    def test_low_source_trust_dampens_contribution(
        self, engine: BayesianConfidenceEngine, now: datetime
    ) -> None:
        full = Evidence(kind=EvidenceKind.NEWS_RELEVANCE, value=1.0, direction_aligned=1)
        weak = Evidence(
            kind=EvidenceKind.NEWS_RELEVANCE, value=1.0, direction_aligned=1, source_trust=0.2
        )
        r_full = engine.evaluate([full], prior_probability=0.5, now=now)
        r_weak = engine.evaluate([weak], prior_probability=0.5, now=now)
        assert r_full.posterior_probability > r_weak.posterior_probability > 0.5


# ─── Decay ───────────────────────────────────────────────────────────────────


class TestFreshnessDecay:
    def test_old_evidence_contributes_less(
        self, engine: BayesianConfidenceEngine, now: datetime
    ) -> None:
        fresh_ev = Evidence(
            kind=EvidenceKind.NEWS_RELEVANCE,
            value=1.0,
            direction_aligned=1,
            observed_at=now,
        )
        stale_ev = Evidence(
            kind=EvidenceKind.NEWS_RELEVANCE,
            value=1.0,
            direction_aligned=1,
            observed_at=now - timedelta(hours=24),  # 4 Halbwertszeiten
        )
        r_fresh = engine.evaluate([fresh_ev], prior_probability=0.5, now=now)
        r_stale = engine.evaluate([stale_ev], prior_probability=0.5, now=now)
        assert r_stale.posterior_probability < r_fresh.posterior_probability
        # Restunsicherheits-Treiber muss Stale-Evidence nennen
        drivers = " ".join(r_stale.residual_uncertainty_drivers)
        assert "freshness" in drivers.lower() or "veraltet" in drivers.lower()

    def test_freshness_naive_datetime_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Evidence(
                kind=EvidenceKind.NEWS_RELEVANCE,
                value=1.0,
                direction_aligned=1,
                observed_at=datetime(2026, 5, 9, 12, 0, 0),  # naive
            )


# ─── Semantik-Helfer ─────────────────────────────────────────────────────────


class TestSemanticHelpers:
    def test_funding_rate_inverts_for_long(
        self, engine: BayesianConfidenceEngine, now: datetime
    ) -> None:
        # Positive Funding + LONG-Signal → Crowd long → contra
        ev_long = build_funding_rate_evidence(
            funding_rate_pct=0.04, signal_is_long=True, observed_at=now
        )
        # Positive Funding + SHORT-Signal → pro
        ev_short = build_funding_rate_evidence(
            funding_rate_pct=0.04, signal_is_long=False, observed_at=now
        )
        r_long = engine.evaluate([ev_long], prior_probability=0.5, now=now)
        r_short = engine.evaluate([ev_short], prior_probability=0.5, now=now)
        assert r_long.posterior_probability < 0.5
        assert r_short.posterior_probability > 0.5

    def test_liquidations_contra_side_fuels_signal(
        self, engine: BayesianConfidenceEngine, now: datetime
    ) -> None:
        ev = build_liquidations_evidence(
            liquidation_volume_usd=80_000_000,
            contra_side_dominant=True,
            signal_is_long=True,
            observed_at=now,
        )
        report = engine.evaluate([ev], prior_probability=0.5, now=now)
        assert report.posterior_probability > 0.5
        assert ev.direction_aligned == 1

    def test_liquidations_same_side_hurts_signal(
        self, engine: BayesianConfidenceEngine, now: datetime
    ) -> None:
        ev = build_liquidations_evidence(
            liquidation_volume_usd=80_000_000,
            contra_side_dominant=False,
            signal_is_long=True,
            observed_at=now,
        )
        report = engine.evaluate([ev], prior_probability=0.5, now=now)
        assert report.posterior_probability < 0.5

    def test_oi_rising_with_aligned_price_supports_signal(
        self, engine: BayesianConfidenceEngine, now: datetime
    ) -> None:
        pro = build_open_interest_evidence(
            oi_change_zscore=2.5,
            price_move_aligned_with_signal=True,
            observed_at=now,
        )
        contra = build_open_interest_evidence(
            oi_change_zscore=2.5,
            price_move_aligned_with_signal=False,
            observed_at=now,
        )
        r_pro = engine.evaluate([pro], prior_probability=0.5, now=now)
        r_contra = engine.evaluate([contra], prior_probability=0.5, now=now)
        assert r_pro.posterior_probability > 0.5
        assert r_contra.posterior_probability < 0.5

    def test_market_regime_trending_with_supports_signal(
        self, engine: BayesianConfidenceEngine, now: datetime
    ) -> None:
        pro = build_market_regime_evidence(regime="trending_with", observed_at=now)
        contra = build_market_regime_evidence(regime="trending_against", observed_at=now)
        unknown = build_market_regime_evidence(regime="unknown", observed_at=now)
        r_pro = engine.evaluate([pro], prior_probability=0.5, now=now)
        r_contra = engine.evaluate([contra], prior_probability=0.5, now=now)
        r_unknown = engine.evaluate([unknown], prior_probability=0.5, now=now)
        assert r_pro.posterior_probability > 0.5
        assert r_contra.posterior_probability < 0.5
        assert r_unknown.posterior_probability == pytest.approx(0.5)
        assert r_unknown.discarded  # unknown regime → direction_aligned=0

    def test_market_regime_unknown_value_rejected(self) -> None:
        with pytest.raises(ValueError):
            build_market_regime_evidence(regime="bizarre")

    def test_on_chain_inflow_to_exchange_is_bearish_for_long(
        self, engine: BayesianConfidenceEngine, now: datetime
    ) -> None:
        ev = build_on_chain_evidence(
            netflow_zscore=2.0,
            inflow_to_exchange=True,
            signal_is_long=True,
            observed_at=now,
        )
        report = engine.evaluate([ev], prior_probability=0.5, now=now)
        assert report.posterior_probability < 0.5

    def test_historical_hit_rate_evidence_factory_handles_low_n(self) -> None:
        ev = build_historical_hit_rate_evidence(hit_rate=0.9, n_observations=2)
        # Low n → magnitude gedämpft
        assert abs(ev.value) < 0.2


# ─── Pydantic-Validierung ────────────────────────────────────────────────────


class TestEvidenceValidation:
    def test_value_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Evidence(kind=EvidenceKind.NEWS_RELEVANCE, value=1.5, direction_aligned=1)
        with pytest.raises(ValidationError):
            Evidence(kind=EvidenceKind.NEWS_RELEVANCE, value=-1.5, direction_aligned=1)

    def test_direction_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Evidence(kind=EvidenceKind.NEWS_RELEVANCE, value=0.5, direction_aligned=2)

    def test_source_trust_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Evidence(
                kind=EvidenceKind.NEWS_RELEVANCE,
                value=0.5,
                direction_aligned=1,
                source_trust=1.5,
            )

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Evidence(
                kind=EvidenceKind.NEWS_RELEVANCE,
                value=0.5,
                direction_aligned=1,
                random_field="x",
            )


# ─── Determinismus ───────────────────────────────────────────────────────────


class TestDeterminism:
    def test_same_input_same_report(
        self, engine: BayesianConfidenceEngine, now: datetime
    ) -> None:
        evidences = [
            build_news_evidence(
                relevance=0.7, sentiment_aligned_with_signal=True, observed_at=now
            ),
            build_funding_rate_evidence(
                funding_rate_pct=0.02, signal_is_long=True, observed_at=now
            ),
            build_market_regime_evidence(regime="ranging", observed_at=now),
        ]
        r1 = engine.evaluate(evidences, prior_probability=0.5, now=now)
        r2 = engine.evaluate(evidences, prior_probability=0.5, now=now)
        assert r1.model_dump() == r2.model_dump()


# ─── Erklär-Output ───────────────────────────────────────────────────────────


class TestExplainability:
    def test_report_separates_pro_contra_neutral_discarded(
        self, engine: BayesianConfidenceEngine, now: datetime
    ) -> None:
        evidences = [
            build_news_evidence(
                relevance=0.9, sentiment_aligned_with_signal=True, observed_at=now
            ),
            build_news_evidence(
                relevance=0.4, sentiment_aligned_with_signal=False, observed_at=now
            ),
            Evidence(kind=EvidenceKind.NEWS_RELEVANCE, value=0.0, direction_aligned=1),  # neutral
            Evidence(kind=EvidenceKind.NEWS_RELEVANCE, value=0.5, direction_aligned=0),  # discard
        ]
        report = engine.evaluate(evidences, prior_probability=0.5, now=now)
        assert len(report.increased) == 1
        assert len(report.decreased) == 1
        assert len(report.neutral) == 1
        assert len(report.discarded) == 1

    def test_increased_sorted_desc_by_contribution(
        self, engine: BayesianConfidenceEngine, now: datetime
    ) -> None:
        weak = build_news_evidence(
            relevance=0.3, sentiment_aligned_with_signal=True, observed_at=now
        )
        strong = build_news_evidence(
            relevance=0.95, sentiment_aligned_with_signal=True, observed_at=now
        )
        report = engine.evaluate([weak, strong], prior_probability=0.5, now=now)
        assert len(report.increased) == 2
        assert report.increased[0].contribution >= report.increased[1].contribution

    def test_residual_drivers_mention_low_evidence(
        self, engine: BayesianConfidenceEngine, now: datetime
    ) -> None:
        weak = build_news_evidence(
            relevance=0.1, sentiment_aligned_with_signal=True, observed_at=now
        )
        report = engine.evaluate([weak], prior_probability=0.5, now=now)
        drivers = " ".join(report.residual_uncertainty_drivers)
        assert "Evidenzmasse" in drivers or "evidence" in drivers.lower()
