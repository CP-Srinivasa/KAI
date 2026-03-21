"""Tests for AnalysisPipeline â€” mocked LLM provider."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.analysis.base.interfaces import LLMAnalysisOutput
from app.analysis.ensemble.provider import EnsembleProvider
from app.analysis.internal_model.provider import InternalModelProvider
from app.analysis.keywords.engine import KeywordEngine
from app.analysis.keywords.watchlist import WatchlistEntry
from app.analysis.pipeline import AnalysisPipeline, PipelineResult
from app.core.domain.document import AnalysisResult, CanonicalDocument
from app.core.enums import AnalysisSource, MarketScope, SentimentLabel


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


def _mock_named_provider(name: str, output: LLMAnalysisOutput):
    provider = AsyncMock()
    provider.provider_name = name
    provider.model = name
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
    assert result.analysis_result.analysis_source == AnalysisSource.RULE


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
    assert result.analysis_result.analysis_source == AnalysisSource.EXTERNAL_LLM


@pytest.mark.asyncio
async def test_pipeline_with_companion_provider_marks_internal_analysis_source():
    llm_out = _make_llm_output()
    provider = _mock_named_provider("companion", llm_out)
    engine = _btc_engine()
    pipeline = AnalysisPipeline(keyword_engine=engine, provider=provider, run_llm=True)

    result = await pipeline.run(_make_doc())

    assert result.analysis_result is not None
    assert result.analysis_result.analysis_source == AnalysisSource.INTERNAL

    result.apply_to_document()

    assert result.document.analysis_source == AnalysisSource.INTERNAL
    assert result.document.provider == "companion"


@pytest.mark.asyncio
async def test_ensemble_openai_wins_sets_external_llm_source():
    llm_out = _make_llm_output()
    openai_provider = _mock_named_provider("openai", llm_out)
    internal_provider = InternalModelProvider(_btc_engine())
    ensemble = EnsembleProvider([openai_provider, internal_provider])

    engine = _btc_engine()
    pipeline = AnalysisPipeline(keyword_engine=engine, provider=ensemble, run_llm=True)
    result = await pipeline.run(_make_doc())

    assert result.analysis_result is not None
    assert result.provider_name == "openai"
    assert result.analysis_result.analysis_source == AnalysisSource.EXTERNAL_LLM
    assert result.trace_metadata == {"ensemble_chain": ["openai", "internal"]}
    result.apply_to_document()
    assert result.document.analysis_source == AnalysisSource.EXTERNAL_LLM
    assert result.document.provider == "openai"
    assert result.document.metadata.get("ensemble_chain") == ["openai", "internal"]

@pytest.mark.asyncio
async def test_ensemble_internal_fallback_sets_internal_source():
    openai_provider = AsyncMock()
    openai_provider.provider_name = "openai"
    openai_provider.model = "gpt-4o"
    openai_provider.analyze = AsyncMock(side_effect=RuntimeError("API Error"))
    internal_provider = InternalModelProvider(_btc_engine())
    ensemble = EnsembleProvider([openai_provider, internal_provider])

    engine = _btc_engine()
    pipeline = AnalysisPipeline(keyword_engine=engine, provider=ensemble, run_llm=True)
    result = await pipeline.run(_make_doc("Bitcoin halving", "BTC halving outlook"))

    assert result.analysis_result is not None
    assert result.provider_name == "internal"
    assert result.analysis_result.analysis_source == AnalysisSource.INTERNAL
    assert result.trace_metadata == {"ensemble_chain": ["openai", "internal"]}
    result.apply_to_document()
    assert result.document.analysis_source == AnalysisSource.INTERNAL
    assert result.document.provider == "internal"
    assert result.document.metadata.get("ensemble_chain") == ["openai", "internal"]


def test_apply_to_document_falls_back_to_llm_market_scope():
    doc = CanonicalDocument(
        url="https://example.com/market-scope",
        title="Market scope fallback",
        market_scope=MarketScope.UNKNOWN,
    )
    llm_output = _make_llm_output()
    analysis_result = AnalysisResult(
        document_id=str(doc.id),
        analysis_source=AnalysisSource.EXTERNAL_LLM,
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
    assert doc.analysis_source == AnalysisSource.EXTERNAL_LLM


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
    assert result.analysis_result.analysis_source == AnalysisSource.RULE

    result.apply_to_document()

    assert result.document.provider == "fallback"
    assert result.document.analysis_source == AnalysisSource.RULE


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
    assert result.analysis_result.analysis_source == AnalysisSource.RULE

    result.apply_to_document()

    assert result.document.provider == "fallback"
    assert result.document.analysis_source == AnalysisSource.RULE


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
    assert doc.analysis_source == AnalysisSource.RULE


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


@pytest.mark.asyncio
async def test_pipeline_with_shadow_provider_success():
    llm_out = _make_llm_output()
    provider = _mock_provider(llm_out)
    shadow_out = _make_llm_output()
    shadow_out.sentiment_label = SentimentLabel.BEARISH
    shadow_out.recommended_priority = 3
    shadow_provider = _mock_named_provider("companion", shadow_out)

    engine = _btc_engine()
    pipeline = AnalysisPipeline(
        keyword_engine=engine,
        provider=provider,
        shadow_provider=shadow_provider,
        run_llm=True
    )

    result = await pipeline.run(_make_doc())

    assert result.success
    assert result.llm_output is not None
    assert result.llm_output.sentiment_label == SentimentLabel.BULLISH

    # Check shadow output was captured natively on the result object
    assert result.shadow_llm_output is not None
    assert result.shadow_llm_output.sentiment_label == SentimentLabel.BEARISH
    assert result.shadow_provider_name == "companion"

    result.apply_to_document()

    # I-51 Shadow Non-Mutation
    assert result.document.provider == "openai"
    assert result.document.analysis_source == AnalysisSource.EXTERNAL_LLM

    # Shadow serialization
    shadow_data = result.document.metadata.get("shadow_analysis")
    assert shadow_data is not None
    assert shadow_data["sentiment_label"] == "bearish"
    assert shadow_data["recommended_priority"] == 3
    assert result.document.metadata.get("shadow_provider") == "companion"


@pytest.mark.asyncio
async def test_pipeline_with_shadow_provider_error_does_not_fail_primary():
    llm_out = _make_llm_output()
    provider = _mock_provider(llm_out)

    shadow_provider = AsyncMock()
    shadow_provider.provider_name = "companion"
    shadow_provider.model = "kai-v1"
    shadow_provider.analyze = AsyncMock(side_effect=RuntimeError("Companion offline"))

    engine = _btc_engine()
    pipeline = AnalysisPipeline(
        keyword_engine=engine,
        provider=provider,
        shadow_provider=shadow_provider,
        run_llm=True
    )

    # I-52 Shadow Error Isolation
    result = await pipeline.run(_make_doc())

    assert result.success
    assert result.llm_output is not None
    assert result.shadow_llm_output is None
    assert result.shadow_error == "Companion offline"

    result.apply_to_document()
    assert result.document.provider == "openai"
    assert "shadow_analysis" not in result.document.metadata


@pytest.mark.asyncio
async def test_pipeline_shadow_provider_runs_with_rule_fallback_primary():
    shadow_out = _make_llm_output()
    shadow_out.short_reasoning = "Companion shadow summary."
    shadow_provider = _mock_named_provider("companion", shadow_out)

    engine = _btc_engine()
    pipeline = AnalysisPipeline(
        keyword_engine=engine,
        provider=None,
        shadow_provider=shadow_provider,
        run_llm=False,
    )

    result = await pipeline.run(_make_doc("Bitcoin halving", "BTC outlook remains active"))

    assert result.analysis_result is not None
    assert result.analysis_result.analysis_source == AnalysisSource.RULE
    assert result.provider_name == "fallback"
    assert result.shadow_llm_output is not None
    assert result.shadow_provider_name == "companion"

    result.apply_to_document()

    assert result.document.provider == "fallback"
    assert result.document.analysis_source == AnalysisSource.RULE
    assert result.document.metadata["shadow_analysis"]["short_reasoning"] == (
        "Companion shadow summary."
    )
