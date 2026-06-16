"""Tests for the pre-analysis crypto-relevance gate (2026-06-16)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.analysis.crypto_relevance import crypto_relevance_verdict
from app.analysis.keywords.engine import KeywordEngine, KeywordHit
from app.analysis.keywords.watchlist import WatchlistEntry
from app.analysis.pipeline import AnalysisPipeline
from app.core.domain.document import CanonicalDocument
from app.core.enums import MarketScope, SentimentLabel


def _btc_engine() -> KeywordEngine:
    return KeywordEngine(
        keywords=frozenset({"halving", "etf"}),
        watchlist_entries=[
            WatchlistEntry(
                symbol="BTC",
                name="Bitcoin",
                aliases=frozenset({"bitcoin"}),
                tags=(),
                category="crypto",
            )
        ],
        entity_aliases=[],
    )


def _mock_provider():
    from app.analysis.base.interfaces import LLMAnalysisOutput

    provider = AsyncMock()
    provider.provider_name = "openai"
    provider.model = "gpt-4o"
    provider.analyze = AsyncMock(
        return_value=LLMAnalysisOutput(
            sentiment_label=SentimentLabel.NEUTRAL,
            sentiment_score=0.0,
            relevance_score=0.5,
            impact_score=0.3,
            confidence_score=0.5,
            novelty_score=0.3,
            spam_probability=0.01,
            market_scope=MarketScope.CRYPTO,
            affected_assets=[],
            short_reasoning="x",
            recommended_priority=3,
            actionable=False,
        )
    )
    return provider


# A non-crypto doc that CLEARS the stub + D-110 low-relevance gates (text > 50
# bytes, ≥4 tags push pre_llm_relevance ≥ 0.12) so the crypto gate is the only
# thing that can skip the LLM.
def _non_crypto_doc() -> CanonicalDocument:
    return CanonicalDocument(
        url="https://example.com/sport",
        title="World Cup kicks off in Toronto with record attendance",
        raw_text="The opening match drew a sold-out crowd as the tournament began across venues.",
        tags=["sports", "worldcup", "toronto", "football"],
    )


def _crypto_doc() -> CanonicalDocument:
    return CanonicalDocument(
        url="https://example.com/btc",
        title="Bitcoin ETF rally",
        raw_text="BTC hits new high amid growing institutional demand for crypto funds globally.",
        tags=["crypto", "etf"],
    )


# ── pure verdict ───────────────────────────────────────────────────────────


def test_verdict_tickers_relevant():
    doc = CanonicalDocument(url="u", title="t", raw_text="x", tickers=["BTC"])
    assert crypto_relevance_verdict(doc, []) == (True, "has_tickers")


def test_verdict_crypto_assets_relevant():
    doc = CanonicalDocument(url="u", title="t", raw_text="x", crypto_assets=["ETH"])
    assert crypto_relevance_verdict(doc, []) == (True, "has_crypto_assets")


def test_verdict_crypto_keyword_hit_relevant():
    doc = CanonicalDocument(url="u", title="t", raw_text="x")
    hits = [KeywordHit(canonical="BTC", category="crypto", occurrences=1)]
    assert crypto_relevance_verdict(doc, hits) == (True, "crypto_keyword_hit")


def test_verdict_non_crypto_hit_does_not_count():
    doc = CanonicalDocument(url="u", title="t", raw_text="x")
    hits = [KeywordHit(canonical="AAPL", category="equity", occurrences=2)]
    assert crypto_relevance_verdict(doc, hits) == (False, "no_crypto_signal")


def test_verdict_no_signal_irrelevant():
    doc = CanonicalDocument(url="u", title="t", raw_text="x")
    assert crypto_relevance_verdict(doc, []) == (False, "no_crypto_signal")


# ── pipeline wiring ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enforce_skips_llm_for_non_crypto():
    provider = _mock_provider()
    pipe = AnalysisPipeline(
        keyword_engine=_btc_engine(), provider=provider, run_llm=True, crypto_gate_mode="enforce"
    )
    result = await pipe.run(_non_crypto_doc())
    provider.analyze.assert_not_called()
    assert result.llm_output is None
    assert "crypto_relevance_gate" in (result.analysis_result.explanation_short or "")


@pytest.mark.asyncio
async def test_enforce_keeps_crypto_doc():
    provider = _mock_provider()
    pipe = AnalysisPipeline(
        keyword_engine=_btc_engine(), provider=provider, run_llm=True, crypto_gate_mode="enforce"
    )
    await pipe.run(_crypto_doc())
    provider.analyze.assert_awaited()  # crypto signal → LLM runs


@pytest.mark.asyncio
async def test_shadow_does_not_skip():
    provider = _mock_provider()
    pipe = AnalysisPipeline(
        keyword_engine=_btc_engine(), provider=provider, run_llm=True, crypto_gate_mode="shadow"
    )
    await pipe.run(_non_crypto_doc())
    provider.analyze.assert_awaited()  # shadow = measure only, LLM still runs


@pytest.mark.asyncio
async def test_off_does_not_skip():
    provider = _mock_provider()
    pipe = AnalysisPipeline(
        keyword_engine=_btc_engine(), provider=provider, run_llm=True, crypto_gate_mode="off"
    )
    await pipe.run(_non_crypto_doc())
    provider.analyze.assert_awaited()


@pytest.mark.asyncio
async def test_enforce_never_skips_trusted_author(tmp_path):
    # A trusted social author bypasses all skip gates, including this one.
    handle = "whale"
    provider = _mock_provider()
    pipe = AnalysisPipeline(
        keyword_engine=_btc_engine(),
        provider=provider,
        run_llm=True,
        crypto_gate_mode="enforce",
        trusted_social_handles=frozenset({handle}),
    )
    from app.core.enums import SourceType

    doc = CanonicalDocument(
        url="https://x.com/whale/1",
        title="gm",
        raw_text="markets look interesting today, no specific asset named here at all",
        author=f"@{handle}",
        source_type=SourceType.SOCIAL_API,
    )
    await pipe.run(doc)
    provider.analyze.assert_awaited()  # trusted author → LLM runs despite no crypto signal
