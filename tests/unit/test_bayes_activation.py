"""Settings → SignalGenerator-Kwargs (Bayesian Activation Helper)."""

from __future__ import annotations

from pathlib import Path

from app.core.settings import LearningSettings, RiskSettings
from app.signals.bayes_activation import build_bayes_signal_kwargs
from app.signals.bayes_journal import DEFAULT_BAYES_AUDIT_PATH
from app.signals.bayesian_confidence import BayesianConfidenceEngine, build_default_engine
from app.signals.generator import SignalGenerator


def _settings(**overrides) -> RiskSettings:
    base = RiskSettings()
    return base.model_copy(update=overrides)


# ── Disabled-Pfad ─────────────────────────────────────────────────────────────


def test_disabled_returns_empty_mapping() -> None:
    s = _settings(bayes_confidence_enabled=False)
    kwargs = build_bayes_signal_kwargs(s)
    assert kwargs == {}


def test_disabled_kwargs_preserve_legacy_signal_generator() -> None:
    s = _settings(bayes_confidence_enabled=False)
    gen = SignalGenerator(**build_bayes_signal_kwargs(s))
    # Legacy-Verhalten: keine engine, kein audit, kein provider
    assert gen._bayes_engine is None  # noqa: SLF001
    assert gen._bayes_audit_path is None  # noqa: SLF001
    assert gen._bayes_extra_evidences_provider is None  # noqa: SLF001


# ── Enabled-Pfad ──────────────────────────────────────────────────────────────


def test_enabled_provides_engine_and_audit_path() -> None:
    s = _settings(bayes_confidence_enabled=True)
    kwargs = build_bayes_signal_kwargs(s)
    assert isinstance(kwargs["bayes_engine"], BayesianConfidenceEngine)
    assert kwargs["bayes_audit_path"] == DEFAULT_BAYES_AUDIT_PATH
    assert kwargs["bayes_shadow_only"] is True
    assert kwargs["min_bayes_confidence"] == 0.0
    assert kwargs["max_bayes_uncertainty"] == 1.0
    assert "bayes_extra_evidences_provider" not in kwargs


def test_enabled_passes_through_thresholds() -> None:
    s = _settings(
        bayes_confidence_enabled=True,
        bayes_confidence_shadow_only=False,
        min_bayes_confidence=0.4,
        max_bayes_uncertainty=0.7,
    )
    kwargs = build_bayes_signal_kwargs(s)
    assert kwargs["bayes_shadow_only"] is False
    assert kwargs["min_bayes_confidence"] == 0.4
    assert kwargs["max_bayes_uncertainty"] == 0.7


def test_explicit_engine_and_audit_override(tmp_path: Path) -> None:
    s = _settings(bayes_confidence_enabled=True)
    custom_engine = build_default_engine()
    custom_path = tmp_path / "audit.jsonl"

    def _provider(_a, _m, _d):
        return ()

    kwargs = build_bayes_signal_kwargs(
        s,
        engine=custom_engine,
        audit_path=custom_path,
        extra_evidences_provider=_provider,
    )
    assert kwargs["bayes_engine"] is custom_engine
    assert kwargs["bayes_audit_path"] == custom_path
    assert kwargs["bayes_extra_evidences_provider"] is _provider


def test_regime_engine_passthrough() -> None:
    from app.market_data.regime_detection import build_default_engine as build_regime_engine

    s = _settings(bayes_confidence_enabled=True)
    regime = build_regime_engine()
    kwargs = build_bayes_signal_kwargs(s, regime_engine=regime)
    assert kwargs["regime_engine"] is regime


def test_regime_engine_omitted_when_bayes_disabled() -> None:
    from app.market_data.regime_detection import build_default_engine as build_regime_engine

    s = _settings(bayes_confidence_enabled=False)
    kwargs = build_bayes_signal_kwargs(s, regime_engine=build_regime_engine())
    assert kwargs == {}, "Regime-Engine darf ohne Bayes-Aktivierung nicht durchgereicht werden"


# ── End-to-End: SignalGenerator mit Activation-Kwargs ────────────────────────


def test_generator_built_from_kwargs_writes_audit(tmp_path: Path) -> None:
    """Aktivator + Generator + Audit-Sidecar laufen zusammen ohne Bruch."""
    from app.core.domain.document import AnalysisResult
    from app.core.enums import SentimentLabel
    from app.market_data.models import MarketDataPoint
    from app.signals.bayes_journal import load_bayes_reports

    s = _settings(bayes_confidence_enabled=True)
    audit = tmp_path / "bayes_audit.jsonl"
    kwargs = build_bayes_signal_kwargs(s, audit_path=audit)
    gen = SignalGenerator(**kwargs)

    analysis = AnalysisResult(
        document_id="doc_act_001",
        sentiment_label=SentimentLabel.BULLISH,
        sentiment_score=0.8,
        relevance_score=0.85,
        impact_score=0.75,
        confidence_score=0.8,
        novelty_score=0.6,
        actionable=True,
        affected_assets=["BTC"],
        tags=["t"],
        spam_probability=0.05,
        explanation_short="thesis>=10ch",
        explanation_long="long",
    )
    md = MarketDataPoint(
        symbol="BTC/USDT",
        timestamp_utc="2026-05-09T12:00:00+00:00",
        price=65_000.0,
        volume_24h=4_000_000.0,
        change_pct_24h=3.5,
        source="mock",
    )
    signal = gen.generate(analysis, md, "BTC/USDT")
    assert signal is not None
    assert signal.bayes_posterior_probability is not None

    rows = load_bayes_reports(audit)
    assert len(rows) == 1
    assert rows[0].decision_id == signal.decision_id


# ── Adaptive-Learning Wiring (Schritt 1) ─────────────────────────────────────


def _learning_settings(**overrides) -> LearningSettings:
    base = LearningSettings()
    return base.model_copy(update=overrides)


def test_no_learning_settings_omits_adaptive_loaders() -> None:
    """Default ``learning_settings=None`` ⇒ kein Verhaltenswechsel."""
    s = _settings(bayes_confidence_enabled=True)
    kwargs = build_bayes_signal_kwargs(s)
    assert "active_calibrator" not in kwargs
    assert "active_min_bayes_confidence" not in kwargs
    assert "reasoning_journal" not in kwargs


def test_disabled_adaptive_learning_omits_loaders(tmp_path: Path) -> None:
    """``adaptive_learning_enabled=False`` ⇒ Loaders werden nicht gebaut."""
    s = _settings(bayes_confidence_enabled=True)
    learning = _learning_settings(
        adaptive_learning_enabled=False,
        snapshot_dir=tmp_path / "snapshots",
        reasoning_journal_path=tmp_path / "rj.jsonl",
    )
    kwargs = build_bayes_signal_kwargs(s, learning_settings=learning)
    assert "active_calibrator" not in kwargs
    assert "active_min_bayes_confidence" not in kwargs
    assert "reasoning_journal" not in kwargs


def test_disabled_bayes_skips_learning_even_if_enabled(tmp_path: Path) -> None:
    """Master-Gate: ohne Bayes keine Learning-Loaders, egal was learning sagt."""
    s = _settings(bayes_confidence_enabled=False)
    learning = _learning_settings(
        adaptive_learning_enabled=True,
        snapshot_dir=tmp_path / "snapshots",
    )
    kwargs = build_bayes_signal_kwargs(s, learning_settings=learning)
    assert kwargs == {}


def test_enabled_adaptive_learning_no_snapshot_yields_inactive_loaders(
    tmp_path: Path,
) -> None:
    """Snapshot fehlt ⇒ Loaders sind ``is_active=False`` (Identity-Verhalten)."""
    from app.audit.structured_reasoning import ReasoningJournal
    from app.learning.active_calibrator import ActiveCalibrator
    from app.learning.active_threshold import ActiveThreshold

    s = _settings(bayes_confidence_enabled=True, min_bayes_confidence=0.42)
    journal_path = tmp_path / "structured_reasoning.jsonl"
    learning = _learning_settings(
        adaptive_learning_enabled=True,
        snapshot_dir=tmp_path / "snapshots",  # leeres / nicht-existentes Verzeichnis
        reasoning_journal_path=journal_path,
    )
    kwargs = build_bayes_signal_kwargs(s, learning_settings=learning)

    cal = kwargs["active_calibrator"]
    threshold = kwargs["active_min_bayes_confidence"]
    journal = kwargs["reasoning_journal"]

    assert isinstance(cal, ActiveCalibrator)
    assert cal.is_active is False  # kein Snapshot ⇒ Identity
    assert cal.version_id is None

    assert isinstance(threshold, ActiveThreshold)
    assert threshold.is_active is False
    assert threshold.value == 0.42  # default aus risk_settings.min_bayes_confidence
    assert threshold.default_value == 0.42

    assert isinstance(journal, ReasoningJournal)
    assert journal.path == journal_path


def test_enabled_adaptive_learning_with_threshold_snapshot(tmp_path: Path) -> None:
    """Snapshot vorhanden ⇒ Threshold lädt operator-approved value."""
    from app.learning.config_snapshot import write_snapshot

    snap_dir = tmp_path / "snapshots"
    write_snapshot(
        parameter_path="signal.thresholds.min_bayes_confidence",
        parameter_set={"value": 0.71, "default": 0.30},
        version_id="pv_test_001",
        activated_at_utc="2026-05-10T08:00:00+00:00",
        activated_by="operator",
        snapshot_dir=snap_dir,
    )

    s = _settings(bayes_confidence_enabled=True, min_bayes_confidence=0.30)
    learning = _learning_settings(
        adaptive_learning_enabled=True,
        snapshot_dir=snap_dir,
        reasoning_journal_path=tmp_path / "rj.jsonl",
    )
    kwargs = build_bayes_signal_kwargs(s, learning_settings=learning)

    threshold = kwargs["active_min_bayes_confidence"]
    assert threshold.is_active is True
    assert threshold.value == 0.71
    assert threshold.default_value == 0.30
    assert threshold.version_id == "pv_test_001"


def test_enabled_adaptive_learning_with_calibrator_snapshot(tmp_path: Path) -> None:
    """Snapshot vorhanden ⇒ Calibrator lädt active bundle (single-form payload)."""
    from app.learning.active_calibrator import DEFAULT_BAYES_CALIBRATOR_PATH
    from app.learning.config_snapshot import write_snapshot

    snap_dir = tmp_path / "snapshots"
    write_snapshot(
        parameter_path=DEFAULT_BAYES_CALIBRATOR_PATH,
        parameter_set={
            "intercept": 0.05,
            "slope": 0.95,
            "n_fitted": 240,
            "is_identity": False,
        },
        version_id="pv_cal_001",
        activated_at_utc="2026-05-10T08:00:00+00:00",
        activated_by="operator",
        snapshot_dir=snap_dir,
    )

    s = _settings(bayes_confidence_enabled=True)
    learning = _learning_settings(
        adaptive_learning_enabled=True,
        snapshot_dir=snap_dir,
        reasoning_journal_path=tmp_path / "rj.jsonl",
    )
    kwargs = build_bayes_signal_kwargs(s, learning_settings=learning)

    cal = kwargs["active_calibrator"]
    assert cal.is_active is True
    assert cal.version_id == "pv_cal_001"


def test_learning_settings_default_off() -> None:
    """``LearningSettings()`` boot-time default = adaptive_learning disabled.

    Ein frisches Deployment darf nie ohne expliziten Operator-Schalter in
    den Active-Calibrator-Pfad rutschen.
    """
    learning = LearningSettings()
    assert learning.adaptive_learning_enabled is False
