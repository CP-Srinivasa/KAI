"""Unit tests für Market Regime Detection Engine.

Pflichtfokus: Verhalten, nicht Implementierung.

Abdeckung:
  - Profile-Vollständigkeit (alle 8 Regimes × alle 8 Features)
  - Z-Scoring (mit + ohne Baseline, fehlende Features)
  - Klassifikation (alle 8 Regime sind erreichbar)
  - Anomaly-Score (extrem out-of-distribution)
  - Volatility-Buckets (4 Klassen)
  - HMM (Viterbi-Pfad, Forward-Posterior, Persistenz)
  - Baseline-Update (mean/stddev aus rohen Beobachtungen)
  - Pydantic-Validierung (naive datetime, leeres profile)
  - Erklär-Output (supporting / opposing / drivers)
  - Determinismus
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.market_data.regime_detection import (
    REGIME_PRIORS,
    REGIME_PROFILES,
    TRANSITION_MATRIX,
    BaselineStats,
    FeatureName,
    MarketRegime,
    RegimeDetectionEngine,
    RegimeObservation,
    VolatilityBucket,
    build_default_engine,
    make_observation,
)

# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> RegimeDetectionEngine:
    return build_default_engine()


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)


def _obs_from_profile(regime: MarketRegime, now: datetime) -> RegimeObservation:
    """Bau eine Beobachtung exakt am Center des Regime-Profils."""
    feats = {f: mu for f, (mu, _) in REGIME_PROFILES[regime].items()}
    return RegimeObservation(timestamp_utc=now, features=feats)


# ─── Profile-Setup ───────────────────────────────────────────────────────────


class TestProfileIntegrity:
    def test_all_eight_regimes_have_all_eight_features(self) -> None:
        for regime, profile in REGIME_PROFILES.items():
            assert set(profile.keys()) == set(FeatureName), f"missing features in {regime}"
            for f, (_mu, sigma) in profile.items():
                assert sigma > 0, f"non-positive sigma in {regime}/{f}"

    def test_priors_sum_to_one(self) -> None:
        assert sum(REGIME_PRIORS.values()) == pytest.approx(1.0, abs=1e-6)

    def test_transition_matrix_rows_sum_to_one(self) -> None:
        for src, row in TRANSITION_MATRIX.items():
            assert set(row.keys()) == set(MarketRegime), f"missing target in {src}"
            assert sum(row.values()) == pytest.approx(1.0, abs=1e-6)

    def test_engine_rejects_profile_with_missing_feature(self) -> None:
        bad_profile = {
            MarketRegime.BULL: dict.fromkeys(list(FeatureName)[:-1], (0.0, 1.0)),  # 7/8
        }
        with pytest.raises(ValueError, match="missing features"):
            RegimeDetectionEngine(profiles=bad_profile)


# ─── Klassifikation: alle 8 Regime erreichbar ────────────────────────────────


class TestRegimeReachability:
    @pytest.mark.parametrize("target", list(MarketRegime))
    def test_observation_at_profile_center_classifies_correctly(
        self, engine: RegimeDetectionEngine, now: datetime, target: MarketRegime
    ) -> None:
        obs = _obs_from_profile(target, now)
        report = engine.classify(obs)
        assert report.classification.primary_regime == target, (
            f"expected {target}, got {report.classification.primary_regime} "
            f"(distribution: {report.classification.distribution})"
        )


class TestSpecificRegimes:
    def test_bull_observation_yields_bull(
        self, engine: RegimeDetectionEngine, now: datetime
    ) -> None:
        obs = make_observation(
            features={
                FeatureName.VOLATILITY: 0.5,
                FeatureName.CORRELATION: -0.3,
                FeatureName.ORDERBOOK_IMBALANCE: 0.6,
                FeatureName.FUNDING_RATE: 0.5,
                FeatureName.STABLECOIN_FLOW: -0.5,
                FeatureName.WHALE_FLOW: 0.7,
                FeatureName.SOCIAL_MOMENTUM: 1.0,
                FeatureName.MACRO_ENVIRONMENT: 0.5,
            },
            timestamp_utc=now,
        )
        r = engine.classify(obs)
        assert r.classification.primary_regime == MarketRegime.BULL
        assert r.classification.primary_probability > 0.3

    def test_panic_observation_yields_panic(
        self, engine: RegimeDetectionEngine, now: datetime
    ) -> None:
        obs = make_observation(
            features={
                FeatureName.VOLATILITY: 2.5,
                FeatureName.CORRELATION: 1.5,
                FeatureName.ORDERBOOK_IMBALANCE: -1.0,
                FeatureName.FUNDING_RATE: -2.0,
                FeatureName.STABLECOIN_FLOW: 2.0,
                FeatureName.WHALE_FLOW: -1.5,
                FeatureName.SOCIAL_MOMENTUM: -2.0,
                FeatureName.MACRO_ENVIRONMENT: -1.0,
            },
            timestamp_utc=now,
        )
        r = engine.classify(obs)
        assert r.classification.primary_regime == MarketRegime.PANIC
        assert r.volatility_bucket == VolatilityBucket.EXTREME

    def test_euphoric_blowoff_yields_correct_bucket(
        self, engine: RegimeDetectionEngine, now: datetime
    ) -> None:
        obs = _obs_from_profile(MarketRegime.EUPHORIC_BLOWOFF, now)
        r = engine.classify(obs)
        assert r.classification.primary_regime == MarketRegime.EUPHORIC_BLOWOFF
        assert r.volatility_bucket in {VolatilityBucket.HIGH, VolatilityBucket.EXTREME}


# ─── Distribution-Vertrag ────────────────────────────────────────────────────


class TestDistributionContract:
    def test_distribution_sums_to_one(self, engine: RegimeDetectionEngine, now: datetime) -> None:
        obs = _obs_from_profile(MarketRegime.BULL, now)
        r = engine.classify(obs)
        assert sum(r.classification.distribution.values()) == pytest.approx(1.0, abs=1e-5)

    def test_distribution_covers_all_eight_regimes(
        self, engine: RegimeDetectionEngine, now: datetime
    ) -> None:
        obs = _obs_from_profile(MarketRegime.BULL, now)
        r = engine.classify(obs)
        assert set(r.classification.distribution.keys()) == set(MarketRegime)

    def test_secondary_is_not_primary(self, engine: RegimeDetectionEngine, now: datetime) -> None:
        obs = _obs_from_profile(MarketRegime.BULL, now)
        r = engine.classify(obs)
        assert r.classification.secondary_regime != r.classification.primary_regime


# ─── Volatility-Buckets ──────────────────────────────────────────────────────


class TestVolatilityBuckets:
    @pytest.mark.parametrize(
        "z_vol,expected",
        [
            (-1.5, VolatilityBucket.LOW),
            (0.0, VolatilityBucket.NORMAL),
            (0.99, VolatilityBucket.NORMAL),
            (1.0, VolatilityBucket.HIGH),
            (2.4, VolatilityBucket.HIGH),
            (2.5, VolatilityBucket.EXTREME),
            (5.0, VolatilityBucket.EXTREME),
        ],
    )
    def test_bucket_thresholds(
        self, engine: RegimeDetectionEngine, now: datetime, z_vol: float, expected: VolatilityBucket
    ) -> None:
        obs = make_observation(features={FeatureName.VOLATILITY: z_vol}, timestamp_utc=now)
        r = engine.classify(obs)
        assert r.volatility_bucket == expected


# ─── Anomaly-Score ───────────────────────────────────────────────────────────


class TestAnomalyDetection:
    def test_observation_at_profile_has_low_anomaly(
        self, engine: RegimeDetectionEngine, now: datetime
    ) -> None:
        obs = _obs_from_profile(MarketRegime.BULL, now)
        r = engine.classify(obs)
        assert r.anomaly_score < 0.5

    def test_extreme_out_of_distribution_yields_high_anomaly(
        self, engine: RegimeDetectionEngine, now: datetime
    ) -> None:
        obs = make_observation(
            features=dict.fromkeys(FeatureName, 10.0),
            timestamp_utc=now,
        )
        r = engine.classify(obs)
        assert r.anomaly_score > 0.95

    def test_anomaly_in_unit_interval(self, engine: RegimeDetectionEngine, now: datetime) -> None:
        for vol in [-5.0, 0.0, 5.0]:
            obs = make_observation(features={FeatureName.VOLATILITY: vol}, timestamp_utc=now)
            r = engine.classify(obs)
            assert 0.0 <= r.anomaly_score <= 1.0


# ─── Z-Scoring + Baseline ────────────────────────────────────────────────────


class TestZScoring:
    def test_no_baseline_treats_features_as_z_scores(
        self, engine: RegimeDetectionEngine, now: datetime
    ) -> None:
        obs = make_observation(
            features={FeatureName.VOLATILITY: 2.0, FeatureName.SOCIAL_MOMENTUM: 1.0},
            timestamp_utc=now,
        )
        r = engine.classify(obs)
        assert r.z_scores[FeatureName.VOLATILITY] == pytest.approx(2.0)
        assert r.z_scores[FeatureName.SOCIAL_MOMENTUM] == pytest.approx(1.0)

    def test_with_baseline_normalizes_raw_values(
        self, engine: RegimeDetectionEngine, now: datetime
    ) -> None:
        baseline = BaselineStats(
            means=dict.fromkeys(FeatureName, 0.0) | {FeatureName.VOLATILITY: 0.20},
            stddevs=dict.fromkeys(FeatureName, 1.0) | {FeatureName.VOLATILITY: 0.05},
            n_observations=100,
        )
        obs = make_observation(
            features={FeatureName.VOLATILITY: 0.30},  # raw
            timestamp_utc=now,
        )
        r = engine.classify(obs, baseline=baseline)
        # (0.30 - 0.20) / 0.05 = 2.0
        assert r.z_scores[FeatureName.VOLATILITY] == pytest.approx(2.0)

    def test_missing_features_default_to_zero(
        self, engine: RegimeDetectionEngine, now: datetime
    ) -> None:
        obs = make_observation(
            features={FeatureName.VOLATILITY: 1.0},  # nur 1 von 8
            timestamp_utc=now,
        )
        r = engine.classify(obs)
        assert r.z_scores[FeatureName.SOCIAL_MOMENTUM] == 0.0


# ─── HMM (Viterbi + Forward) ─────────────────────────────────────────────────


class TestHMM:
    def test_classify_sequence_returns_path_with_correct_length(
        self, engine: RegimeDetectionEngine, now: datetime
    ) -> None:
        obs_list = [
            _obs_from_profile(MarketRegime.BULL, now + timedelta(minutes=i)) for i in range(5)
        ]
        r = engine.classify_sequence(obs_list)
        assert r.hmm_path is not None
        assert len(r.hmm_path) == 5

    def test_hmm_persistence_keeps_state_across_steady_observations(
        self, engine: RegimeDetectionEngine, now: datetime
    ) -> None:
        obs_list = [
            _obs_from_profile(MarketRegime.BULL, now + timedelta(minutes=i)) for i in range(8)
        ]
        r = engine.classify_sequence(obs_list)
        # Persistenz 0.7 + alle Beobachtungen bullish → kompletter Pfad bull
        assert all(s == MarketRegime.BULL for s in r.hmm_path)
        assert r.classification.primary_regime == MarketRegime.BULL

    def test_hmm_transitions_to_neighboring_regime(
        self, engine: RegimeDetectionEngine, now: datetime
    ) -> None:
        # 5x bull, dann 5x euphoric_blowoff (Nachbar von bull)
        seq = []
        for i in range(5):
            seq.append(_obs_from_profile(MarketRegime.BULL, now + timedelta(minutes=i)))
        for i in range(5):
            seq.append(
                _obs_from_profile(MarketRegime.EUPHORIC_BLOWOFF, now + timedelta(minutes=5 + i))
            )
        r = engine.classify_sequence(seq)
        assert r.hmm_path is not None
        assert r.hmm_path[0] == MarketRegime.BULL
        assert r.hmm_path[-1] == MarketRegime.EUPHORIC_BLOWOFF

    def test_classify_sequence_empty_raises(self, engine: RegimeDetectionEngine) -> None:
        with pytest.raises(ValueError):
            engine.classify_sequence([])


# ─── Baseline-Update ─────────────────────────────────────────────────────────


class TestBaselineUpdate:
    def test_update_baseline_aggregates_mean_and_stddev(self) -> None:
        now = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)
        observations = [
            make_observation(
                features={FeatureName.VOLATILITY: float(i % 5)},
                timestamp_utc=now + timedelta(minutes=i),
            )
            for i in range(50)
        ]
        baseline = RegimeDetectionEngine.update_baseline(observations)
        assert baseline.n_observations == 50
        # 0,1,2,3,4 wiederholt → mean=2.0
        assert baseline.means[FeatureName.VOLATILITY] == pytest.approx(2.0, abs=1e-6)
        assert baseline.stddevs[FeatureName.VOLATILITY] > 0

    def test_update_baseline_too_few_raises(self) -> None:
        now = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)
        observations = [
            make_observation(features={FeatureName.VOLATILITY: 0.5}, timestamp_utc=now)
            for _ in range(5)
        ]
        with pytest.raises(ValueError, match="at least"):
            RegimeDetectionEngine.update_baseline(observations, min_observations=30)

    def test_update_baseline_constant_feature_gets_safe_stddev(self) -> None:
        now = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)
        observations = [
            make_observation(features={FeatureName.VOLATILITY: 0.5}, timestamp_utc=now)
            for _ in range(50)
        ]
        baseline = RegimeDetectionEngine.update_baseline(observations)
        assert baseline.stddevs[FeatureName.VOLATILITY] == 1.0  # safe-default

    def test_update_baseline_missing_feature_uses_safe_default(self) -> None:
        now = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)
        observations = [
            make_observation(features={FeatureName.VOLATILITY: 1.0}, timestamp_utc=now)
            for _ in range(50)
        ]
        baseline = RegimeDetectionEngine.update_baseline(observations)
        # SOCIAL_MOMENTUM in keiner observation → mean=0, stddev=1
        assert baseline.means[FeatureName.SOCIAL_MOMENTUM] == 0.0
        assert baseline.stddevs[FeatureName.SOCIAL_MOMENTUM] == 1.0


# ─── Validierung ─────────────────────────────────────────────────────────────


class TestValidation:
    def test_naive_datetime_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RegimeObservation(
                timestamp_utc=datetime(2026, 5, 9, 12, 0, 0),
                features={FeatureName.VOLATILITY: 1.0},
            )

    def test_baseline_with_nonpositive_stddev_rejected(self) -> None:
        with pytest.raises(ValidationError):
            BaselineStats(
                means=dict.fromkeys(FeatureName, 0.0),
                stddevs=dict.fromkeys(FeatureName, 0.0),
                n_observations=10,
            )

    def test_extra_feature_in_observation_rejected_via_dict_typing(self) -> None:
        # Features-Dict hat fixen Key-Typ FeatureName → fremder String fliegt
        with pytest.raises(ValidationError):
            RegimeObservation(
                timestamp_utc=datetime.now(UTC),
                features={"unknown_feature": 1.0},  # type: ignore[dict-item]
            )


# ─── Erklär-Output ───────────────────────────────────────────────────────────


class TestExplanations:
    def test_supporting_features_are_top_3(
        self, engine: RegimeDetectionEngine, now: datetime
    ) -> None:
        obs = _obs_from_profile(MarketRegime.BULL, now)
        r = engine.classify(obs)
        assert len(r.supporting_features) == 3
        # supporting sind die mit höchster log-likelihood-contribution
        lls = [c.log_likelihood_contribution for c in r.supporting_features]
        assert lls == sorted(lls, reverse=True)

    def test_opposing_features_are_bottom_3(
        self, engine: RegimeDetectionEngine, now: datetime
    ) -> None:
        # Bull-Profil + Vol-Spike = grobe Diskrepanz beim Vol-Feature
        obs = make_observation(
            features={
                FeatureName.VOLATILITY: 5.0,  # weit von bull-mean=0.5
                FeatureName.SOCIAL_MOMENTUM: 1.0,
            },
            timestamp_utc=now,
        )
        r = engine.classify(obs)
        opposing_features = {c.feature for c in r.opposing_features}
        # Volatility sollte unter den widersprechenden Features sein
        assert FeatureName.VOLATILITY in opposing_features

    def test_explanations_mention_primary_and_volatility_bucket(
        self, engine: RegimeDetectionEngine, now: datetime
    ) -> None:
        obs = _obs_from_profile(MarketRegime.BULL, now)
        r = engine.classify(obs)
        joined = " | ".join(r.explanations)
        assert "bull" in joined.lower()
        assert "bucket" in joined.lower()

    def test_low_observation_count_drives_uncertainty(
        self, engine: RegimeDetectionEngine, now: datetime
    ) -> None:
        obs = make_observation(
            features={FeatureName.VOLATILITY: 0.5},  # 1/8
            timestamp_utc=now,
        )
        r = engine.classify(obs)
        joined = " | ".join(r.residual_uncertainty_drivers)
        assert "Wenige Features" in joined or "1/8" in joined

    def test_high_anomaly_drives_uncertainty(
        self, engine: RegimeDetectionEngine, now: datetime
    ) -> None:
        obs = make_observation(features=dict.fromkeys(FeatureName, 10.0), timestamp_utc=now)
        r = engine.classify(obs)
        joined = " | ".join(r.residual_uncertainty_drivers)
        assert "Anomalie" in joined


# ─── Determinismus ───────────────────────────────────────────────────────────


def test_same_input_same_output(engine: RegimeDetectionEngine, now: datetime) -> None:
    obs = _obs_from_profile(MarketRegime.BULL, now)
    r1 = engine.classify(obs)
    r2 = engine.classify(obs)
    assert r1.model_dump() == r2.model_dump()


def test_sequence_classification_is_deterministic(
    engine: RegimeDetectionEngine, now: datetime
) -> None:
    obs = [_obs_from_profile(MarketRegime.BULL, now + timedelta(minutes=i)) for i in range(5)]
    r1 = engine.classify_sequence(obs)
    r2 = engine.classify_sequence(obs)
    assert r1.model_dump() == r2.model_dump()
