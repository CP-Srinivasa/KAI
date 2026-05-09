"""Integration: SignalGenerator × BayesianConfidenceEngine.

Validiert Schatten-Modus, Hard-Gate und Schema-Stabilität.

Pflichtfokus:
  - Default (engine=None) → Bayes-Felder bleiben None, kein Verhaltenswandel.
  - engine + shadow_only=True → Felder gefüllt, Signal *nicht* abgelehnt.
  - engine + shadow_only=False + scharfes Gate → Signal abgelehnt.
  - Posterior wird vom Prior (analysis.confidence_score) ausgehend angehoben,
    wenn Volume + Regime-Trend bestätigen.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.domain.document import AnalysisResult
from app.core.enums import SentimentLabel
from app.market_data.models import MarketDataPoint
from app.signals.bayes_journal import load_bayes_reports
from app.signals.bayesian_confidence import (
    build_default_engine,
    build_funding_rate_evidence,
)
from app.signals.generator import SignalGenerator
from app.signals.models import SignalDirection


def _make_analysis(
    *,
    sentiment_label: SentimentLabel = SentimentLabel.BULLISH,
    sentiment_score: float = 0.8,
    relevance_score: float = 0.85,
    impact_score: float = 0.75,
    confidence_score: float = 0.80,
    novelty_score: float = 0.65,
    actionable: bool = True,
    spam_probability: float = 0.05,
    document_id: str = "doc_bayes_001",
) -> AnalysisResult:
    return AnalysisResult(
        document_id=document_id,
        sentiment_label=sentiment_label,
        sentiment_score=sentiment_score,
        relevance_score=relevance_score,
        impact_score=impact_score,
        confidence_score=confidence_score,
        novelty_score=novelty_score,
        actionable=actionable,
        affected_assets=["BTC", "BTC/USDT"],
        tags=["etf", "bullish"],
        spam_probability=spam_probability,
        explanation_short="BTC ETF approval likely.",
        explanation_long="Long-form analysis.",
    )


def _make_market_data(
    *,
    price: float = 65_000.0,
    change_pct_24h: float = 3.5,
    volume_24h: float = 4_000_000.0,
    is_stale: bool = False,
    source: str = "mock",
) -> MarketDataPoint:
    return MarketDataPoint(
        symbol="BTC/USDT",
        timestamp_utc="2026-05-09T12:00:00+00:00",
        price=price,
        volume_24h=volume_24h,
        change_pct_24h=change_pct_24h,
        source=source,
        is_stale=is_stale,
    )


# ── Default-Pfad ──────────────────────────────────────────────────────────────


def test_without_engine_bayes_fields_are_none() -> None:
    gen = SignalGenerator()
    signal = gen.generate(_make_analysis(), _make_market_data(), "BTC/USDT")
    assert signal is not None
    assert signal.bayes_prior_probability is None
    assert signal.bayes_posterior_probability is None
    assert signal.bayes_confidence_score is None
    assert signal.bayes_uncertainty_score is None
    assert signal.bayes_evidence_weight is None


# ── Schatten-Modus ────────────────────────────────────────────────────────────


def test_shadow_mode_attaches_fields_without_blocking() -> None:
    gen = SignalGenerator(
        bayes_engine=build_default_engine(),
        bayes_shadow_only=True,
        # Bewusst absurd scharfe Gates — dürfen im Schatten-Modus *nicht* greifen
        min_bayes_confidence=0.99,
        max_bayes_uncertainty=0.01,
    )
    signal = gen.generate(_make_analysis(), _make_market_data(), "BTC/USDT")
    assert signal is not None, "shadow_only darf niemals filtern"
    assert signal.bayes_prior_probability is not None
    assert signal.bayes_posterior_probability is not None
    assert 0.0 < signal.bayes_posterior_probability < 1.0
    assert 0.0 <= signal.bayes_confidence_score <= 1.0
    assert 0.0 <= signal.bayes_uncertainty_score <= 1.0
    assert signal.bayes_evidence_weight >= 0.0
    # Prior == analysis.confidence_score (vor σ-Update via Evidences)
    assert signal.bayes_prior_probability == pytest.approx(0.80, abs=1e-3)


def test_supporting_market_data_lifts_posterior_above_prior() -> None:
    gen = SignalGenerator(
        bayes_engine=build_default_engine(),
        bayes_shadow_only=True,
        # Legacy-Confidence-Filter aufweichen, damit niedrige Priors die
        # Bayes-Berechnung überhaupt erreichen.
        min_confidence=0.5,
    )
    # Bullish + aligned price-up + high volume + trending → Posterior ↑
    signal = gen.generate(
        _make_analysis(confidence_score=0.55),
        _make_market_data(change_pct_24h=4.0, volume_24h=10_000_000.0),
        "BTC/USDT",
    )
    assert signal is not None
    assert signal.bayes_posterior_probability > signal.bayes_prior_probability


# ── Hard-Gate ─────────────────────────────────────────────────────────────────


def test_hard_gate_rejects_below_min_confidence() -> None:
    gen = SignalGenerator(
        bayes_engine=build_default_engine(),
        bayes_shadow_only=False,
        min_bayes_confidence=0.99,  # praktisch unerreichbar
        max_bayes_uncertainty=1.0,
    )
    signal = gen.generate(_make_analysis(), _make_market_data(), "BTC/USDT")
    assert signal is None


def test_hard_gate_rejects_above_max_uncertainty() -> None:
    gen = SignalGenerator(
        bayes_engine=build_default_engine(),
        bayes_shadow_only=False,
        min_bayes_confidence=0.0,
        max_bayes_uncertainty=0.01,  # praktisch immer überschritten
    )
    signal = gen.generate(_make_analysis(), _make_market_data(), "BTC/USDT")
    assert signal is None


def test_hard_gate_passes_with_loose_thresholds() -> None:
    gen = SignalGenerator(
        bayes_engine=build_default_engine(),
        bayes_shadow_only=False,
        min_bayes_confidence=0.0,
        max_bayes_uncertainty=1.0,
    )
    signal = gen.generate(_make_analysis(), _make_market_data(), "BTC/USDT")
    assert signal is not None


# ── Settings-Vertrag ──────────────────────────────────────────────────────────


def test_risk_settings_carry_bayes_flags_with_safe_defaults() -> None:
    from app.core.settings import RiskSettings

    s = RiskSettings()
    assert s.bayes_confidence_enabled is False
    assert s.bayes_confidence_shadow_only is True
    assert s.min_bayes_confidence == 0.0
    assert s.max_bayes_uncertainty == 1.0


# ── Audit-Sidecar ─────────────────────────────────────────────────────────────


def test_audit_sidecar_written_when_path_set(tmp_path: Path) -> None:
    audit = tmp_path / "bayes_audit.jsonl"
    gen = SignalGenerator(
        bayes_engine=build_default_engine(),
        bayes_audit_path=audit,
    )
    signal = gen.generate(_make_analysis(), _make_market_data(), "BTC/USDT")
    assert signal is not None
    entries = load_bayes_reports(audit)
    assert len(entries) == 1
    assert entries[0].decision_id == signal.decision_id
    assert entries[0].symbol == "BTC/USDT"
    assert entries[0].direction == "long"
    assert entries[0].report["posterior_probability"] == signal.bayes_posterior_probability


def test_audit_sidecar_silent_when_path_unset(tmp_path: Path) -> None:
    gen = SignalGenerator(bayes_engine=build_default_engine())
    signal = gen.generate(_make_analysis(), _make_market_data(), "BTC/USDT")
    assert signal is not None
    # Default-Pfad wird *nicht* implizit beschrieben — nur bei explizit gesetztem path
    assert not (tmp_path / "bayes_confidence_audit.jsonl").exists()


# ── Extra-Evidences-Provider (Funding etc.) ───────────────────────────────────


def test_extra_evidences_provider_lowers_posterior_for_crowded_long() -> None:
    """Hohe positive Funding-Rate signalisiert überfüllte Long-Seite → Posterior ↓."""
    captured = {}

    def provider(analysis, market_data, direction):
        captured["direction"] = direction
        # +5 bp Funding pro 8h → contra-LONG via Helper-Inversion
        return [
            build_funding_rate_evidence(
                funding_rate_pct=0.0005,
                signal_is_long=(direction == SignalDirection.LONG),
            )
        ]

    base = SignalGenerator(bayes_engine=build_default_engine(), bayes_shadow_only=True)
    boosted = SignalGenerator(
        bayes_engine=build_default_engine(),
        bayes_shadow_only=True,
        bayes_extra_evidences_provider=provider,
    )

    md = _make_market_data()
    a = _make_analysis()
    s_base = base.generate(a, md, "BTC/USDT")
    s_boost = boosted.generate(a, md, "BTC/USDT")

    assert captured["direction"] == SignalDirection.LONG
    assert s_base is not None and s_boost is not None
    assert s_boost.bayes_posterior_probability < s_base.bayes_posterior_probability


# ── Regime-Engine-Verdrahtung ─────────────────────────────────────────────────


def test_regime_engine_disabled_uses_legacy_heuristic() -> None:
    """Ohne regime_engine bleibt das change_pct_24h-Mapping aktiv (source_trust=1.0)."""
    gen = SignalGenerator(bayes_engine=build_default_engine(), bayes_shadow_only=True)
    signal = gen.generate(_make_analysis(), _make_market_data(change_pct_24h=4.0), "BTC/USDT")
    assert signal is not None
    assert signal.bayes_evidence_weight is not None


def test_regime_engine_enabled_attaches_engine_classification_to_evidence() -> None:
    """Mit regime_engine ändert sich source_id der Regime-Evidence."""
    from app.market_data.regime_detection import build_default_engine as build_regime_engine

    captured = {}

    def _spy_provider(analysis, market_data, direction):
        return ()

    gen = SignalGenerator(
        bayes_engine=build_default_engine(),
        bayes_shadow_only=True,
        regime_engine=build_regime_engine(),
        bayes_extra_evidences_provider=_spy_provider,
    )
    # Direkt _regime_for_bayes prüfen (kapselt das Mapping ohne Eval-Overhead)
    label, trust, source_id = gen._regime_for_bayes(  # noqa: SLF001
        analysis=_make_analysis(sentiment_score=0.9),
        market_data=_make_market_data(change_pct_24h=4.0, volume_24h=10_000_000.0),
        direction=SignalDirection.LONG,
        price_aligned=True,
    )
    captured["label"] = label
    captured["trust"] = trust
    captured["source"] = source_id
    assert label in {"trending_with", "trending_against", "ranging", "volatile"}
    assert 0.0 <= trust <= 1.0
    assert source_id.startswith("regime_engine:")


def test_regime_engine_trust_bounded_by_primary_probability() -> None:
    """Vertrag: trust = primary_probability · (1 − anomaly_score) ∈ [0, primary_p].

    Stärkere Aussage als "trust ∈ [0,1]": Engine-Klassifikations-Confidence
    dämpft die Bayes-Evidence, Anomaly dämpft sie weiter.
    """
    from app.market_data.regime_detection import (
        FeatureName,
        make_observation,
    )
    from app.market_data.regime_detection import (
        build_default_engine as build_regime_engine,
    )

    regime_engine = build_regime_engine()
    gen = SignalGenerator(
        bayes_engine=build_default_engine(),
        bayes_shadow_only=True,
        regime_engine=regime_engine,
    )

    analysis = _make_analysis(sentiment_score=0.9)
    md = _make_market_data(change_pct_24h=2.0)
    _, trust, _ = gen._regime_for_bayes(  # noqa: SLF001
        analysis=analysis,
        market_data=md,
        direction=SignalDirection.LONG,
        price_aligned=True,
    )
    # Engine direkt für Vergleich: gleiche 2-Feature-Beobachtung
    expected_obs = make_observation(
        features={FeatureName.VOLATILITY: abs(2.0 / 4.0), FeatureName.SOCIAL_MOMENTUM: 0.9 * 2.0}
    )
    expected_report = regime_engine.classify(expected_obs)
    expected_trust = expected_report.classification.primary_probability * (
        1.0 - expected_report.anomaly_score
    )
    assert trust == pytest.approx(expected_trust, abs=1e-6)
    assert 0.0 <= trust <= expected_report.classification.primary_probability


def test_regime_engine_inverts_label_for_short_direction() -> None:
    """BULL-Klassifikation + LONG-Signal = trending_with; + SHORT-Signal = trending_against."""
    from app.market_data.regime_detection import build_default_engine as build_regime_engine

    gen = SignalGenerator(
        bayes_engine=build_default_engine(),
        bayes_shadow_only=True,
        regime_engine=build_regime_engine(),
    )
    # Bullish-prägnante Beobachtung (positiv vol + positiv sentiment)
    long_label, _, _ = gen._regime_for_bayes(  # noqa: SLF001
        analysis=_make_analysis(sentiment_score=0.9),
        market_data=_make_market_data(change_pct_24h=2.0),
        direction=SignalDirection.LONG,
        price_aligned=True,
    )
    short_label, _, _ = gen._regime_for_bayes(  # noqa: SLF001
        analysis=_make_analysis(sentiment_score=0.9),
        market_data=_make_market_data(change_pct_24h=2.0),
        direction=SignalDirection.SHORT,
        price_aligned=False,
    )
    # Wenn Engine "bullish-ish" klassifiziert, kehrt das Label bei SHORT
    if long_label == "trending_with":
        assert short_label == "trending_against"
    elif long_label == "trending_against":
        assert short_label == "trending_with"


def test_extra_evidences_provider_failure_is_swallowed() -> None:
    def boom(analysis, market_data, direction):
        raise RuntimeError("provider broken")

    gen = SignalGenerator(
        bayes_engine=build_default_engine(),
        bayes_shadow_only=True,
        bayes_extra_evidences_provider=boom,
    )
    signal = gen.generate(_make_analysis(), _make_market_data(), "BTC/USDT")
    # Signal kommt trotzdem — Engine läuft mit den Default-Evidences
    assert signal is not None
    assert signal.bayes_confidence_score is not None
