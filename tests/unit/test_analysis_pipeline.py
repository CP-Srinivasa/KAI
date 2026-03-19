"""Tests for AnalysisPipeline â€” mocked LLM provider."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.analysis.base.interfaces import LLMAnalysisOutput
from app.analysis.keywords.engine import KeywordEngine
from app.analysis.keywords.watchlist import WatchlistEntry
from app.analysis.pipeline import AnalysisPipeline, PipelineResult
from app.core.domain.document import AnalysisResult, CanonicalDocument
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


@pytest.mark.asyncio
async def test_pipeline_keyword_stage_no_provider():
    engine = _btc_engine()
    pipeline = AnalysisPipeline(keyword_engine=engine, provider=None)

    result = await pipeline.run(_make_doc("Bitcoin halving approaches", "BTC price analysis"))

    assert result.success
    assert any(h.canonical == "BTC" for h in result.keyword_hits)
    assert any(h.canonical == "halving" for h in result.keyword_hits)
    assert result.llm_output is None
    assert result.analysis_result is not None
    assert result.analysis_result.explanation_short.startswith("Rule-based fallback analysis")
    assert result.analysis_result.affected_assets == ["BTC"]
    assert result.analysis_result.spam_probability >= 0.0


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
    assert result.analysis_result is not None
    assert result.analysis_result.relevance_score == 0.0


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
    assert result.analysis_result.document_id == str(result.document.id)
    assert result.analysis_result.sentiment_label == SentimentLabel.BULLISH
    assert isinstance(result.analysis_result.explanation_short, str)


def test_apply_to_document_falls_back_to_llm_market_scope():
    doc = CanonicalDocument(
        url="https://example.com/market-scope",
        title="Market scope fallback",
        market_scope=MarketScope.UNKNOWN,
    )
    llm_output = _make_llm_output()
    analysis_result = AnalysisResult(
        document_id=str(doc.id),
        sentiment_label=llm_output.sentiment_label,
        sentiment_score=llm_output.sentiment_score,
        relevance_score=llm_output.relevance_score,
        impact_score=llm_output.impact_score,
        confidence_score=llm_output.confidence_score,
        novelty_score=llm_output.novelty_score,
        market_scope=None,
        affected_assets=llm_output.affected_assets,
        affected_sectors=llm_output.affected_sectors,
        event_type=llm_output.event_type,
        explanation_short=llm_output.short_reasoning or "",
        explanation_long=llm_output.long_reasoning or "",
        actionable=llm_output.actionable,
        tags=llm_output.tags,
        spam_probability=llm_output.spam_probability,
    )

    result = PipelineResult(document=doc, llm_output=llm_output, analysis_result=analysis_result)
    result.apply_to_document()

    assert doc.market_scope == MarketScope.CRYPTO


@pytest.mark.asyncio
async def test_pipeline_run_llm_false_uses_fallback_analysis():
    llm_out = _make_llm_output()
    provider = _mock_provider(llm_out)
    engine = _btc_engine()
    pipeline = AnalysisPipeline(keyword_engine=engine, provider=provider, run_llm=False)

    result = await pipeline.run(_make_doc())

    provider.analyze.assert_not_called()
    assert result.llm_output is None
    assert result.analysis_result is not None
    assert "disabled" in result.analysis_result.explanation_short.lower()


@pytest.mark.asyncio
async def test_pipeline_llm_error_uses_rule_fallback():
    provider = AsyncMock()
    provider.provider_name = "openai"
    provider.model = "gpt-4o"
    provider.analyze = AsyncMock(side_effect=RuntimeError("API unavailable"))
    engine = _btc_engine()
    pipeline = AnalysisPipeline(keyword_engine=engine, provider=provider)

    result = await pipeline.run(_make_doc())

    assert result.success
    assert result.error is None
    assert result.llm_output is None
    assert result.analysis_result is not None
    assert "failed" in result.analysis_result.explanation_short.lower()
    assert result.keyword_hits is not None


def test_apply_to_document_with_fallback_analysis_sets_scores_and_entities():
    engine = _btc_engine()
    pipeline = AnalysisPipeline(keyword_engine=engine, provider=None)
    doc = CanonicalDocument(
        url="https://example.com/fallback",
        title="Bitcoin regulation outlook",
        raw_text="BTC regulation update with halving context.",
    )

    result = asyncio.run(pipeline.run(doc))
    assert result.analysis_result is not None

    result.apply_to_document()

    assert doc.priority_score is not None
    assert doc.relevance_score is not None
    assert doc.credibility_score is not None
    assert "BTC" in doc.tickers


@pytest.mark.asyncio
async def test_run_batch_returns_all_results():
    engine = _btc_engine()
    pipeline = AnalysisPipeline(keyword_engine=engine)
    docs = [CanonicalDocument(url=f"https://example.com/{i}", title=f"Doc {i}") for i in range(5)]

    results = await pipeline.run_batch(docs)

    assert len(results) == 5
    assert all(isinstance(r, PipelineResult) for r in results)


@pytest.mark.asyncio
async def test_run_batch_concurrency():
    engine = _btc_engine()
    llm_out = _make_llm_output()
    provider = _mock_provider(llm_out)
    pipeline = AnalysisPipeline(keyword_engine=engine, provider=provider)
    docs = [
        CanonicalDocument(url=f"https://example.com/{i}", title=f"BTC doc {i}") for i in range(8)
    ]

    results = await pipeline.run_batch(docs)

    assert len(results) == 8
    assert provider.analyze.call_count == 8
