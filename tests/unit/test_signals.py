"""Unit tests for the Signal Engine (SignalGenerator + SignalCandidate)."""

from __future__ import annotations

import pytest

from app.core.domain.document import AnalysisResult
from app.core.enums import SentimentLabel
from app.market_data.models import MarketDataPoint
from app.signals.generator import SignalGenerator
from app.signals.models import SignalDirection, SignalState

# ── Fixtures / factories ──────────────────────────────────────────────────────


def _make_analysis(
    *,
    sentiment_label: SentimentLabel = SentimentLabel.BULLISH,
    sentiment_score: float = 0.8,
    relevance_score: float = 0.85,
    impact_score: float = 0.75,
    confidence_score: float = 0.80,
    novelty_score: float = 0.65,
    actionable: bool = True,
    affected_assets: list[str] | None = None,
    tags: list[str] | None = None,
    spam_probability: float = 0.05,
    explanation_short: str = "BTC likely to rally on ETF approval news.",
    explanation_long: str = "Detailed analysis here.",
    document_id: str = "doc_test_001",
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
        affected_assets=affected_assets or ["BTC", "BTC/USDT"],
        tags=tags or ["etf", "bullish"],
        spam_probability=spam_probability,
        explanation_short=explanation_short,
        explanation_long=explanation_long,
    )


def _make_market_data(
    *,
    symbol: str = "BTC/USDT",
    price: float = 65000.0,
    change_pct_24h: float = 3.5,
    volume_24h: float = 1_000_000.0,
    is_stale: bool = False,
    source: str = "mock",
) -> MarketDataPoint:
    return MarketDataPoint(
        symbol=symbol,
        timestamp_utc="2026-03-21T10:00:00+00:00",
        price=price,
        volume_24h=volume_24h,
        change_pct_24h=change_pct_24h,
        source=source,
        is_stale=is_stale,
    )


def _generator(**kwargs) -> SignalGenerator:
    defaults = {
        "min_confidence": 0.75,
        "min_confluence": 2,
        "stop_loss_pct": 2.5,
        "take_profit_pct": 5.0,
    }
    defaults.update(kwargs)
    return SignalGenerator(**defaults)


# ── Happy path ────────────────────────────────────────────────────────────────


def test_bullish_analysis_produces_long_signal():
    gen = _generator()
    analysis = _make_analysis(sentiment_label=SentimentLabel.BULLISH, sentiment_score=0.8)
    md = _make_market_data(price=65000.0)
    signal = gen.generate(analysis, md, "BTC/USDT")
    assert signal is not None
    assert signal.direction == SignalDirection.LONG
    assert signal.symbol == "BTC/USDT"
    assert signal.mode == "paper"


def test_bearish_analysis_produces_short_signal():
    gen = _generator()
    analysis = _make_analysis(sentiment_label=SentimentLabel.BEARISH, sentiment_score=-0.8)
    md = _make_market_data(price=65000.0)
    signal = gen.generate(analysis, md, "BTC/USDT")
    assert signal is not None
    assert signal.direction == SignalDirection.SHORT


def test_signal_candidate_is_frozen():
    gen = _generator()
    analysis = _make_analysis()
    md = _make_market_data()
    signal = gen.generate(analysis, md, "BTC/USDT")
    assert signal is not None
    with pytest.raises((AttributeError, TypeError)):
        signal.direction = SignalDirection.SHORT  # type: ignore[misc]


def test_signal_has_all_mandatory_fields():
    gen = _generator()
    signal = gen.generate(_make_analysis(), _make_market_data(), "BTC/USDT")
    assert signal is not None
    assert signal.decision_id.startswith("dec_")
    assert signal.timestamp_utc
    assert signal.thesis
    assert len(signal.supporting_factors) > 0
    assert len(signal.contradictory_factors) > 0
    assert signal.invalidation_condition
    assert signal.risk_assessment
    assert signal.source_document_id == "doc_test_001"


def test_long_stop_loss_below_entry():
    gen = _generator(stop_loss_pct=2.5)
    signal = gen.generate(
        _make_analysis(sentiment_label=SentimentLabel.BULLISH),
        _make_market_data(price=65000.0),
        "BTC/USDT",
    )
    assert signal is not None
    assert signal.stop_loss_price is not None
    assert signal.stop_loss_price < signal.entry_price


def test_short_stop_loss_above_entry():
    gen = _generator(stop_loss_pct=2.5)
    signal = gen.generate(
        _make_analysis(sentiment_label=SentimentLabel.BEARISH, sentiment_score=-0.8),
        _make_market_data(price=65000.0),
        "BTC/USDT",
    )
    assert signal is not None
    assert signal.stop_loss_price is not None
    assert signal.stop_loss_price > signal.entry_price


def test_take_profit_2_to_1_risk_reward_long():
    gen = _generator(stop_loss_pct=2.5, take_profit_pct=5.0)
    signal = gen.generate(_make_analysis(), _make_market_data(price=100.0), "BTC/USDT")
    assert signal is not None
    assert signal.take_profit_price is not None
    # tp distance should be 2x sl distance
    sl_dist = signal.entry_price - signal.stop_loss_price  # type: ignore[operator]
    tp_dist = signal.take_profit_price - signal.entry_price
    assert abs(tp_dist / sl_dist - 2.0) < 0.01


def test_approval_and_execution_state_default_pending():
    gen = _generator()
    signal = gen.generate(_make_analysis(), _make_market_data(), "BTC/USDT")
    assert signal is not None
    assert signal.approval_state == SignalState.PENDING
    assert signal.execution_state == SignalState.PENDING


# ── Filter: returns None ──────────────────────────────────────────────────────


def test_returns_none_if_no_market_data():
    gen = _generator()
    signal = gen.generate(_make_analysis(), None, "BTC/USDT")
    assert signal is None


def test_returns_none_if_price_zero():
    gen = _generator()
    signal = gen.generate(_make_analysis(), _make_market_data(price=0.0), "BTC/USDT")
    assert signal is None


def test_returns_none_if_stale_data():
    gen = _generator()
    signal = gen.generate(_make_analysis(), _make_market_data(is_stale=True), "BTC/USDT")
    assert signal is None


def test_returns_none_if_confidence_too_low():
    gen = _generator(min_confidence=0.75)
    analysis = _make_analysis(confidence_score=0.60)
    signal = gen.generate(analysis, _make_market_data(), "BTC/USDT")
    assert signal is None


def test_returns_none_if_not_actionable():
    gen = _generator()
    analysis = _make_analysis(actionable=False)
    signal = gen.generate(analysis, _make_market_data(), "BTC/USDT")
    assert signal is None


def test_returns_none_if_neutral_sentiment():
    gen = _generator()
    analysis = _make_analysis(sentiment_label=SentimentLabel.NEUTRAL, sentiment_score=0.0)
    signal = gen.generate(analysis, _make_market_data(), "BTC/USDT")
    assert signal is None


def test_returns_none_if_mixed_sentiment():
    gen = _generator()
    analysis = _make_analysis(sentiment_label=SentimentLabel.MIXED, sentiment_score=0.2)
    signal = gen.generate(analysis, _make_market_data(), "BTC/USDT")
    assert signal is None


def test_returns_none_if_confluence_too_low():
    # Force all analysis dimensions to 0 AND flat market → confluence=0 < min=3
    gen = _generator(min_confluence=3)
    analysis = _make_analysis(
        impact_score=0.3,
        relevance_score=0.4,
        novelty_score=0.2,
        affected_assets=[],
        sentiment_score=0.3,
        sentiment_label=SentimentLabel.BULLISH,
    )
    # change=0.5% < 2% threshold → no momentum; volume=0 < threshold → no volume confirm
    md = _make_market_data(change_pct_24h=0.5, volume_24h=0.0)
    signal = gen.generate(analysis, md, "BTC/USDT")
    assert signal is None


# ── Confluence calculation ────────────────────────────────────────────────────


def test_confluence_max_7():
    """All 7 dimensions contribute: 5 analysis + price momentum + volume confirm."""
    gen = _generator()
    analysis = _make_analysis(
        impact_score=0.9,
        relevance_score=0.9,
        novelty_score=0.9,
        affected_assets=["BTC"],
        sentiment_score=0.9,
    )
    # change=3.5% >= 2% (LONG direction) → momentum; volume=1M >= threshold → confirm
    md = _make_market_data(change_pct_24h=3.5, volume_24h=1_000_000.0)
    signal = gen.generate(analysis, md, "BTC/USDT")
    assert signal is not None
    assert signal.confluence_count == 7


def test_confluence_max_5_analysis_only():
    """5 analysis dimensions when market data contributes 0 (no momentum, no volume)."""
    gen = _generator(volume_threshold_usd=10_000_000.0, price_momentum_threshold_pct=50.0)
    analysis = _make_analysis(
        impact_score=0.9,
        relevance_score=0.9,
        novelty_score=0.9,
        affected_assets=["BTC"],
        sentiment_score=0.9,
    )
    md = _make_market_data(change_pct_24h=1.0, volume_24h=500_000.0)
    signal = gen.generate(analysis, md, "BTC/USDT")
    assert signal is not None
    assert signal.confluence_count == 5


def test_confluence_partial():
    gen = _generator(min_confluence=2)
    # 2 analysis points: high impact + asset match; 0 market points
    analysis = _make_analysis(
        impact_score=0.7,
        relevance_score=0.5,  # below 0.7
        novelty_score=0.3,  # below 0.5
        affected_assets=["BTC"],
        sentiment_score=0.4,  # below 0.6 abs
    )
    # no momentum, no volume
    md = _make_market_data(change_pct_24h=0.5, volume_24h=0.0)
    signal = gen.generate(analysis, md, "BTC/USDT")
    assert signal is not None
    assert signal.confluence_count == 2


# ── Market regime / volatility ────────────────────────────────────────────────


def test_market_regime_volatile_on_large_change():
    gen = _generator()
    md = _make_market_data(change_pct_24h=6.5)
    signal = gen.generate(_make_analysis(), md, "BTC/USDT")
    assert signal is not None
    assert signal.market_regime == "volatile"


def test_market_regime_trending_on_moderate_change():
    gen = _generator()
    md = _make_market_data(change_pct_24h=3.0)
    signal = gen.generate(_make_analysis(), md, "BTC/USDT")
    assert signal is not None
    assert signal.market_regime == "trending"


def test_market_regime_ranging_on_small_change():
    gen = _generator()
    md = _make_market_data(change_pct_24h=0.5)
    signal = gen.generate(_make_analysis(), md, "BTC/USDT")
    assert signal is not None
    assert signal.market_regime == "ranging"


def test_volatility_state_extreme():
    gen = _generator()
    md = _make_market_data(change_pct_24h=9.0)
    signal = gen.generate(_make_analysis(), md, "BTC/USDT")
    assert signal is not None
    assert signal.volatility_state == "extreme"


def test_volatility_state_low():
    gen = _generator()
    md = _make_market_data(change_pct_24h=0.3)
    signal = gen.generate(_make_analysis(), md, "BTC/USDT")
    assert signal is not None
    assert signal.volatility_state == "low"
