"""Tests for AnalysisPipeline — mocked LLM provider."""

from unittest.mock import AsyncMock

import pytest

from app.analysis.base.interfaces import LLMAnalysisOutput
from app.analysis.keywords.engine import KeywordEngine
from app.analysis.keywords.watchlist import WatchlistEntry
from app.analysis.pipeline import AnalysisPipeline, PipelineResult
from app.core.domain.document import AnalysisResult, CanonicalDocument
from app.core.enums import MarketScope, SentimentLabel

# ── Fixtures ──────────────────────────────────────────────────────────────────


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


def _mock_provider(output: LLMAnalysisOutput):
    provider = AsyncMock()
    provider.provider_name = "openai"
    provider.model = "gpt-4o"
    provider.analyze = AsyncMock(return_value=output)
    return provider


def _make_llm_output() -> LLMAnalysisOutput:
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
        short_reasoning="BTC halving approaching.",
        recommended_priority=7,
        actionable=True,
    )


def _make_doc(
    title: str = "Bitcoin ETF rally",
    text: str = "BTC hits new high",
) -> CanonicalDocument:
    return CanonicalDocument(url="https://example.com/1", title=title, raw_text=text)


# ── Keyword-only pipeline (no LLM) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_keyword_stage_no_provider():
    engine = _btc_engine()
    pipeline = AnalysisPipeline(keyword_engine=engine, provider=None)
    result = await pipeline.run(_make_doc("Bitcoin halving approaches", "BTC price analysis"))
    assert result.success
    assert any(h.canonical == "BTC" for h in result.keyword_hits)
    assert any(h.canonical == "halving" for h in result.keyword_hits)
    assert result.llm_output is None
    assert result.analysis_result is None


@pytest.mark.asyncio
async def test_pipeline_entity_mentions_extracted():
    engine = _btc_engine()
    pipeline = AnalysisPipeline(keyword_engine=engine)
    result = await pipeline.run(_make_doc("Bitcoin news", "BTC and bitcoin mentioned"))
    assert any(m.name == "BTC" for m in result.entity_mentions)


@pytest.mark.asyncio
async def test_pipeline_no_hits_on_irrelevant_text():
    engine = _btc_engine()
    pipeline = AnalysisPipeline(keyword_engine=engine)
    result = await pipeline.run(_make_doc("Weather forecast", "It will be sunny tomorrow"))
    assert result.keyword_hits == []
    assert result.entity_mentions == []


# ── Full pipeline with LLM provider ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_with_llm_provider():
    llm_out = _make_llm_output()
    provider = _mock_provider(llm_out)
    engine = _btc_engine()
    pipeline = AnalysisPipeline(keyword_engine=engine, provider=provider, run_llm=True)

    result = await pipeline.run(_make_doc())

    assert result.success
    assert result.llm_output is not None
    assert result.llm_output.sentiment_label == SentimentLabel.BULLISH
    assert result.analysis_result is not None
    assert isinstance(result.analysis_result, AnalysisResult)
    assert result.analysis_result.provider == "openai"
    assert result.analysis_result.model == "gpt-4o"
    assert result.analysis_result.document_id == result.document.id


@pytest.mark.asyncio
async def test_pipeline_run_llm_false_skips_provider():
    llm_out = _make_llm_output()
    provider = _mock_provider(llm_out)
    engine = _btc_engine()
    pipeline = AnalysisPipeline(keyword_engine=engine, provider=provider, run_llm=False)

    result = await pipeline.run(_make_doc())

    provider.analyze.assert_not_called()
    assert result.llm_output is None
    assert result.analysis_result is None


@pytest.mark.asyncio
async def test_pipeline_llm_error_captured():
    provider = AsyncMock()
    provider.provider_name = "openai"
    provider.model = "gpt-4o"
    provider.analyze = AsyncMock(side_effect=RuntimeError("API unavailable"))
    engine = _btc_engine()
    pipeline = AnalysisPipeline(keyword_engine=engine, provider=provider)

    result = await pipeline.run(_make_doc())

    assert not result.success
    assert "API unavailable" in result.error
    assert result.llm_output is None
    # keyword stage should still have run before the error
    assert result.keyword_hits is not None


# ── run_batch ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_batch_returns_all_results():
    engine = _btc_engine()
    pipeline = AnalysisPipeline(keyword_engine=engine)
    docs = [
        CanonicalDocument(url=f"https://example.com/{i}", title=f"Doc {i}")
        for i in range(5)
    ]
    results = await pipeline.run_batch(docs)
    assert len(results) == 5
    assert all(isinstance(r, PipelineResult) for r in results)


@pytest.mark.asyncio
async def test_run_batch_concurrency():
    """Verify all 8 docs complete even with semaphore(5) limit."""
    engine = _btc_engine()
    llm_out = _make_llm_output()
    provider = _mock_provider(llm_out)
    pipeline = AnalysisPipeline(keyword_engine=engine, provider=provider)
    docs = [
        CanonicalDocument(url=f"https://example.com/{i}", title=f"BTC doc {i}")
        for i in range(8)
    ]
    results = await pipeline.run_batch(docs)
    assert len(results) == 8
    assert provider.analyze.call_count == 8
