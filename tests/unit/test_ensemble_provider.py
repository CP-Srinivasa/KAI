"""Tests for EnsembleProvider — ordered provider selection with guaranteed fallback.

Verifies:
- Uses first available provider (priority order)
- Falls back to next provider when one fails
- Guaranteed result as long as InternalModelProvider is included
- provider_name reflects which provider was used (via model attribute)
- Raises RuntimeError only if ALL providers fail (should never happen in practice)
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.analysis.base.interfaces import LLMAnalysisOutput
from app.analysis.ensemble.provider import EnsembleProvider
from app.analysis.internal_model.provider import InternalModelProvider
from app.analysis.keywords.engine import KeywordEngine
from app.analysis.keywords.watchlist import WatchlistEntry
from app.core.enums import MarketScope, SentimentLabel


def _make_llm_output(provider_name: str = "openai") -> LLMAnalysisOutput:
    return LLMAnalysisOutput(
        sentiment_label=SentimentLabel.BULLISH,
        sentiment_score=0.8,
        relevance_score=0.9,
        impact_score=0.7,
        confidence_score=0.85,
        novelty_score=0.6,
        spam_probability=0.01,
        market_scope=MarketScope.CRYPTO,
        affected_assets=["BTC"],
        actionable=True,
        recommended_priority=8,
    )


def _mock_provider(name: str, output: LLMAnalysisOutput | None = None, fail: bool = False):
    provider = AsyncMock()
    provider.provider_name = name
    provider.model = f"{name}-model"
    if fail:
        provider.analyze = AsyncMock(side_effect=RuntimeError(f"{name} unavailable"))
    else:
        provider.analyze = AsyncMock(return_value=output or _make_llm_output(name))
    return provider


def _btc_engine() -> KeywordEngine:
    return KeywordEngine(
        keywords=frozenset({"halving", "etf"}),
        watchlist_entries=[
            WatchlistEntry("BTC", "Bitcoin", frozenset({"bitcoin"}), (), "crypto")
        ],
        entity_aliases=[],
    )


@pytest.mark.asyncio
async def test_ensemble_uses_first_provider_when_available():
    """When first provider succeeds, its output is used."""
    openai = _mock_provider("openai")
    internal = InternalModelProvider(_btc_engine())
    ensemble = EnsembleProvider(providers=[openai, internal])

    result = await ensemble.analyze("Bitcoin ETF news", "BTC price up")

    openai.analyze.assert_called_once()
    assert result.sentiment_label == SentimentLabel.BULLISH
    assert ensemble.model == "openai"


@pytest.mark.asyncio
async def test_ensemble_falls_back_when_first_fails():
    """When first provider fails, next provider is tried."""
    failing_openai = _mock_provider("openai", fail=True)
    internal = InternalModelProvider(_btc_engine())
    ensemble = EnsembleProvider(providers=[failing_openai, internal])

    result = await ensemble.analyze("Bitcoin halving", "BTC ETF")

    assert result is not None
    assert ensemble.model == "internal"


@pytest.mark.asyncio
async def test_ensemble_internal_only_always_succeeds():
    """EnsembleProvider with only InternalModelProvider never fails."""
    ensemble = EnsembleProvider(providers=[InternalModelProvider(_btc_engine())])
    result = await ensemble.analyze("Test", "text")
    assert result is not None
    assert result.sentiment_label == SentimentLabel.NEUTRAL


@pytest.mark.asyncio
async def test_ensemble_all_fail_raises_runtime_error():
    """When ALL providers fail, EnsembleProvider raises RuntimeError."""
    p1 = _mock_provider("openai", fail=True)
    p2 = _mock_provider("anthropic", fail=True)
    ensemble = EnsembleProvider(providers=[p1, p2])

    with pytest.raises(RuntimeError, match="All ensemble providers failed"):
        await ensemble.analyze("Test", "text")


def test_ensemble_provider_name_reflects_all_providers():
    """provider_name must list all providers for traceability."""
    p1 = _mock_provider("openai")
    p2 = InternalModelProvider(_btc_engine())
    ensemble = EnsembleProvider(providers=[p1, p2])
    assert "openai" in ensemble.provider_name
    assert "internal" in ensemble.provider_name


def test_ensemble_empty_providers_raises():
    """EnsembleProvider with empty list must raise ValueError."""
    with pytest.raises(ValueError):
        EnsembleProvider(providers=[])


@pytest.mark.asyncio
async def test_ensemble_three_tier_openai_first_internal_fallback():
    """Full three-tier scenario: openai → anthropic → internal."""
    openai = _mock_provider("openai", fail=True)
    anthropic = _mock_provider("anthropic", fail=True)
    internal = InternalModelProvider(_btc_engine())

    ensemble = EnsembleProvider(providers=[openai, anthropic, internal])
    result = await ensemble.analyze("Bitcoin regulation", "BTC ETH")

    assert result is not None
    assert ensemble.model == "internal"
    assert result.actionable is False  # InternalModelProvider is conservative
