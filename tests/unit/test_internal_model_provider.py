"""Tests for InternalModelProvider — Tier 2 analyst.

Verifies:
- Always produces a result without API key
- Sentiment is always NEUTRAL (conservative, rule-based cannot determine direction)
- actionable is always False (conservative, human review gate)
- priority ceiling ≤ 5 (I-13)
- Keyword-matched assets land in affected_assets
- provider_name = "internal"
- Handles empty text without crashing
"""

from __future__ import annotations

import pytest

from app.analysis.internal_model.provider import InternalModelProvider
from app.analysis.keywords.engine import KeywordEngine
from app.analysis.keywords.watchlist import WatchlistEntry
from app.core.enums import MarketScope, SentimentLabel


def _engine() -> KeywordEngine:
    return KeywordEngine(
        keywords=frozenset({"halving", "etf", "regulation", "hack"}),
        watchlist_entries=[
            WatchlistEntry(
                symbol="BTC",
                name="Bitcoin",
                aliases=frozenset({"bitcoin"}),
                tags=(),
                category="crypto",
            ),
            WatchlistEntry(
                symbol="ETH",
                name="Ethereum",
                aliases=frozenset({"ethereum"}),
                tags=(),
                category="crypto",
            ),
        ],
        entity_aliases=[],
    )


def test_provider_name_is_internal():
    provider = InternalModelProvider(_engine())
    assert provider.provider_name == "internal"


def test_model_is_rule_heuristic():
    provider = InternalModelProvider(_engine())
    assert provider.model is not None
    assert "rule" in provider.model or "heuristic" in provider.model


@pytest.mark.asyncio
async def test_sentiment_always_neutral():
    """Rule-based analysis cannot determine bullish/bearish — must be NEUTRAL."""
    provider = InternalModelProvider(_engine())
    result = await provider.analyze(
        title="Bitcoin ETF approval expected",
        text="BTC price surges on ETF news, halving imminent",
    )
    assert result.sentiment_label == SentimentLabel.NEUTRAL


@pytest.mark.asyncio
async def test_actionable_always_false():
    """Internal model never marks documents as actionable — human review required."""
    provider = InternalModelProvider(_engine())
    result = await provider.analyze(
        title="Bitcoin halving tomorrow",
        text="BTC will halve, price expected to rise significantly",
    )
    assert result.actionable is False


@pytest.mark.asyncio
async def test_recommended_priority_at_most_five():
    """I-13: Internal model recommended_priority must not exceed 5."""
    provider = InternalModelProvider(_engine())
    result = await provider.analyze(
        title="Bitcoin BTC halving ETF regulation hack",
        text="bitcoin btc halving etf regulation hack " * 10,
    )
    assert result.recommended_priority <= 5


@pytest.mark.asyncio
async def test_keyword_matched_assets_in_affected_assets():
    """Keyword-matched asset symbols must appear in affected_assets."""
    provider = InternalModelProvider(_engine())
    result = await provider.analyze(
        title="Bitcoin analysis",
        text="BTC price ETH rally",
    )
    assert "BTC" in result.affected_assets


@pytest.mark.asyncio
async def test_empty_text_does_not_crash():
    """Provider must handle empty title and text without raising."""
    provider = InternalModelProvider(_engine())
    result = await provider.analyze(title="", text="")
    assert result is not None
    assert result.relevance_score == 0.0
    assert result.impact_score == 0.0


@pytest.mark.asyncio
async def test_market_scope_reflects_keyword_categories():
    """Crypto keywords must produce CRYPTO scope, not UNKNOWN."""
    provider = InternalModelProvider(_engine())
    result = await provider.analyze(
        title="Bitcoin halving",
        text="BTC ETH bitcoin ethereum halving",
    )
    assert result.market_scope in (MarketScope.CRYPTO, MarketScope.MIXED)


@pytest.mark.asyncio
async def test_scores_are_bounded():
    """All float scores must be within [0.0, 1.0]."""
    provider = InternalModelProvider(_engine())
    result = await provider.analyze(
        title="Bitcoin ETF regulation",
        text="BTC ETH halving regulation " * 5,
    )
    assert 0.0 <= result.relevance_score <= 1.0
    assert 0.0 <= result.impact_score <= 1.0
    assert 0.0 <= result.confidence_score <= 1.0
    assert 0.0 <= result.novelty_score <= 1.0
    assert 0.0 <= result.spam_probability <= 1.0


@pytest.mark.asyncio
async def test_context_tickers_in_affected_assets():
    """Tickers passed via context must appear in affected_assets."""
    provider = InternalModelProvider(_engine())
    result = await provider.analyze(
        title="Macro news",
        text="General market commentary",
        context={"tickers": ["AAPL", "MSFT"]},
    )
    assert "AAPL" in result.affected_assets
    assert "MSFT" in result.affected_assets
