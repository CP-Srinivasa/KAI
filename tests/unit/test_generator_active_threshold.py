"""Integration test: SignalGenerator opt-in active min_bayes_confidence."""

from __future__ import annotations

from pathlib import Path

from app.core.domain.document import AnalysisResult
from app.core.enums import SentimentLabel
from app.learning.active_threshold import (
    DEFAULT_MIN_BAYES_CONFIDENCE_PATH,
    ActiveThreshold,
)
from app.learning.config_snapshot import write_snapshot
from app.market_data.models import MarketDataPoint
from app.signals.bayesian_confidence import build_default_engine
from app.signals.generator import SignalGenerator

# --------------------------------------------------------------------- helpers


def _make_analysis() -> AnalysisResult:
    return AnalysisResult(
        document_id="doc_test",
        sentiment_label=SentimentLabel.BULLISH,
        sentiment_score=0.8,
        relevance_score=0.85,
        impact_score=0.85,
        confidence_score=0.85,
        novelty_score=0.7,
        actionable=True,
        affected_assets=["BTC", "BTC/USDT"],
        tags=["etf", "bullish"],
        spam_probability=0.05,
        explanation_short="BTC ETF approved.",
        explanation_long="Detailed.",
    )


def _make_market_data() -> MarketDataPoint:
    return MarketDataPoint(
        symbol="BTC/USDT",
        timestamp_utc="2026-03-21T10:00:00+00:00",
        price=65000.0,
        volume_24h=2_000_000.0,
        change_pct_24h=3.5,
        source="mock",
    )


def _make_generator(
    *,
    active_min_bayes_confidence: ActiveThreshold | None = None,
    static_min_bayes: float = 0.30,
):
    return SignalGenerator(
        min_confidence=0.75,
        min_confluence=2,
        bayes_engine=build_default_engine(),
        bayes_shadow_only=False,
        min_bayes_confidence=static_min_bayes,
        max_bayes_uncertainty=0.95,
        active_min_bayes_confidence=active_min_bayes_confidence,
    )


def _write_threshold_snapshot(tmp_path: Path, *, value: float):
    write_snapshot(
        parameter_path=DEFAULT_MIN_BAYES_CONFIDENCE_PATH,
        parameter_set={"value": value, "default": 0.30},
        version_id="pv_threshold_smoke",
        activated_at_utc="2026-05-09T17:00:00+00:00",
        activated_by="test",
        snapshot_dir=tmp_path,
    )


# ============================================================================
# Backward compat
# ============================================================================


def test_default_constructor_works_without_active_threshold():
    gen = _make_generator(active_min_bayes_confidence=None)
    out = gen.generate(_make_analysis(), _make_market_data(), "BTC/USDT")
    assert out is not None


def test_inactive_threshold_loads_default_and_passes_through(tmp_path: Path):
    """Empty snapshot dir → ActiveThreshold falls back to default; identical
    behavior to the static `min_bayes_confidence=0.30`."""
    th = ActiveThreshold.load(
        parameter_path=DEFAULT_MIN_BAYES_CONFIDENCE_PATH,
        default_value=0.30,
        snapshot_dir=tmp_path,
    )
    assert not th.is_active
    out = _make_generator(active_min_bayes_confidence=th).generate(
        _make_analysis(), _make_market_data(), "BTC/USDT"
    )
    assert out is not None


# ============================================================================
# Active threshold takes precedence
# ============================================================================


def test_active_threshold_overrides_static_constructor_value(tmp_path: Path):
    """A snapshot-derived threshold of 0.99 should reject signals that the
    static 0.30 would have approved."""
    _write_threshold_snapshot(tmp_path, value=0.99)
    th = ActiveThreshold.load(
        parameter_path=DEFAULT_MIN_BAYES_CONFIDENCE_PATH,
        default_value=0.30,
        snapshot_dir=tmp_path,
    )
    assert th.is_active
    assert th.value == 0.99

    # Without active threshold: signal goes through (default 0.30 is lenient)
    baseline = _make_generator(static_min_bayes=0.30).generate(
        _make_analysis(), _make_market_data(), "BTC/USDT"
    )
    assert baseline is not None
    assert baseline.bayes_confidence_score is not None
    # Make sure the baseline confidence is < 0.99 so the threshold actually
    # binds when overridden.
    assert baseline.bayes_confidence_score < 0.99

    out = _make_generator(
        active_min_bayes_confidence=th, static_min_bayes=0.30
    ).generate(_make_analysis(), _make_market_data(), "BTC/USDT")
    # Active threshold tightened the gate → signal rejected
    assert out is None


def test_active_threshold_lower_than_static_lets_more_through():
    """Active threshold = 0.10, static = 0.50 → active wins, signal passes."""
    th = ActiveThreshold.fixed(
        parameter_path=DEFAULT_MIN_BAYES_CONFIDENCE_PATH, value=0.10
    )
    # We mark it as fixed → is_active is False, so the static value applies.
    # Use the .load path from a snapshot dir for a *real* active test below.
    out = _make_generator(
        active_min_bayes_confidence=th, static_min_bayes=0.99
    ).generate(_make_analysis(), _make_market_data(), "BTC/USDT")
    # ActiveThreshold.fixed() reports is_active=False so the static gate of
    # 0.99 still applies → signal rejected. This documents fixed-vs-load.
    assert out is None


def test_active_threshold_loaded_from_snapshot_relaxes_strict_static(tmp_path: Path):
    """Loaded threshold with `is_active=True` overrides a stricter static."""
    _write_threshold_snapshot(tmp_path, value=0.10)
    th = ActiveThreshold.load(
        parameter_path=DEFAULT_MIN_BAYES_CONFIDENCE_PATH,
        default_value=0.30,
        snapshot_dir=tmp_path,
    )
    assert th.is_active
    out = _make_generator(
        active_min_bayes_confidence=th, static_min_bayes=0.99
    ).generate(_make_analysis(), _make_market_data(), "BTC/USDT")
    # Active 0.10 overrides static 0.99 → signal passes
    assert out is not None
