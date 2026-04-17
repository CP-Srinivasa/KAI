"""Tests for AnalysisPipeline shadow-run behavior (I-51-I-57).

Historical reference: docs/archive/sprint10_shadow_run_contract.md

These tests verify:
- shadow output is stored in document.metadata
- primary analysis is unchanged when shadow runs
- shadow failure is non-blocking
- no shadow fields when shadow_provider=None
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.analysis.base.interfaces import LLMAnalysisOutput
from app.analysis.keywords.engine import KeywordEngine
from app.analysis.keywords.watchlist import WatchlistEntry
from app.analysis.pipeline import AnalysisPipeline
from app.core.domain.document import CanonicalDocument
from app.core.enums import MarketScope, SentimentLabel

# -- helpers --


def _empty_engine() -> KeywordEngine:
    return KeywordEngine(
        keywords=frozenset(),
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


def _make_doc(
    title: str = "BTC rally",
    text: str = "Bitcoin surged today amid renewed institutional demand for digital assets.",
) -> CanonicalDocument:
    return CanonicalDocument(url="https://example.com/btc", title=title, raw_text=text)


def _mock_provider(
    provider_name: str = "openai",
    model: str = "gpt-4o",
    sentiment: SentimentLabel = SentimentLabel.BEARISH,
    priority: int = 3,
) -> AsyncMock:
    output = LLMAnalysisOutput(
        sentiment_label=sentiment,
        sentiment_score=0.7,
        relevance_score=0.5,
        impact_score=0.4,
        confidence_score=0.8,
        novelty_score=0.5,
        spam_probability=0.02,
        market_scope=MarketScope.CRYPTO,
        affected_assets=["BTC"],
        affected_sectors=[],
        event_type="price_movement",
        short_reasoning="test",
        long_reasoning="test long",
        actionable=False,
        tags=["crypto"],
        recommended_priority=priority,
    )
    provider = AsyncMock()
    provider.provider_name = provider_name
    provider.model = model
    provider.analyze = AsyncMock(return_value=output)
    return provider


# -- I-56 + I-57: shadow output stored in metadata --


@pytest.mark.asyncio
async def test_shadow_run_stores_output_in_document_metadata() -> None:
    """Shadow output written to doc.metadata after apply_to_document() (I-53, I-57)."""
    primary = _mock_provider("openai", "gpt-4o", SentimentLabel.BEARISH, priority=3)
    shadow = _mock_provider("anthropic", "claude-opus", SentimentLabel.BULLISH, priority=9)

    pipeline = AnalysisPipeline(_empty_engine(), primary, shadow_provider=shadow)
    doc = _make_doc()
    result = await pipeline.run(doc)

    assert result.shadow_llm_output is not None
    assert result.shadow_provider_name is not None

    result.apply_to_document()

    assert "shadow_analysis" in result.document.metadata
    assert "shadow_provider" in result.document.metadata
    shadow_data = result.document.metadata["shadow_analysis"]
    assert shadow_data["sentiment_label"] == SentimentLabel.BULLISH.value


@pytest.mark.asyncio
async def test_shadow_run_does_not_affect_primary_result() -> None:
    """Primary analysis_result and scores are unchanged by shadow (I-54)."""
    primary = _mock_provider("openai", sentiment=SentimentLabel.BEARISH, priority=3)
    shadow = _mock_provider("anthropic", sentiment=SentimentLabel.BULLISH, priority=9)

    pipeline = AnalysisPipeline(_empty_engine(), primary, shadow_provider=shadow)
    doc = _make_doc()
    result = await pipeline.run(doc)
    result.apply_to_document()

    # Primary dominates all scores
    assert result.document.sentiment_label == SentimentLabel.BEARISH
    # Shadow does not mutate priority_score (it comes from primary via compute_priority)
    assert result.analysis_result is not None
    assert result.analysis_result.sentiment_label == SentimentLabel.BEARISH


# -- I-56: shadow failure is non-blocking --


@pytest.mark.asyncio
async def test_shadow_failure_is_non_blocking() -> None:
    """Shadow exception leaves primary result intact (I-52, I-56)."""
    primary = _mock_provider("openai", sentiment=SentimentLabel.BULLISH, priority=7)
    shadow = AsyncMock()
    shadow.provider_name = "shadow"
    shadow.model = "kai-v1"
    shadow.analyze = AsyncMock(side_effect=RuntimeError("Shadow endpoint unreachable"))

    pipeline = AnalysisPipeline(_empty_engine(), primary, shadow_provider=shadow)
    doc = _make_doc()
    result = await pipeline.run(doc)

    # Primary succeeds
    assert result.success is True
    assert result.analysis_result is not None
    assert result.analysis_result.sentiment_label == SentimentLabel.BULLISH

    # Shadow captured error, did not produce output
    assert result.shadow_error is not None
    assert "unreachable" in result.shadow_error.lower()
    assert result.shadow_llm_output is None

    result.apply_to_document()
    assert "shadow_analysis" not in result.document.metadata


# -- I-56: no shadow run when shadow_provider is None --


@pytest.mark.asyncio
async def test_no_shadow_run_when_shadow_provider_is_none() -> None:
    """Without shadow_provider, no shadow fields on result or metadata (I-56)."""
    primary = _mock_provider("openai", sentiment=SentimentLabel.NEUTRAL, priority=5)

    pipeline = AnalysisPipeline(_empty_engine(), primary)  # no shadow_provider
    doc = _make_doc()
    result = await pipeline.run(doc)

    assert result.shadow_llm_output is None
    assert result.shadow_provider_name is None
    assert result.shadow_error is None

    result.apply_to_document()
    assert "shadow_analysis" not in result.document.metadata
    assert "shadow_provider" not in result.document.metadata


# -- I-57: shadow persistence to document.metadata --


@pytest.mark.asyncio
async def test_shadow_data_present_in_document_metadata_for_db_write() -> None:
    """After apply_to_document(), shadow data is in doc.metadata ready for update_analysis.

    Verifies I-57: update_analysis should receive res.document.metadata (not trace_metadata)
    so shadow data reaches the DB. This test confirms the data is IN doc.metadata post-apply.
    """
    primary = _mock_provider("openai")
    shadow = _mock_provider("shadow", "kai-v1")

    pipeline = AnalysisPipeline(_empty_engine(), primary, shadow_provider=shadow)
    doc = _make_doc()
    result = await pipeline.run(doc)
    result.apply_to_document()

    # Both shadow keys must be in doc.metadata (what update_analysis would receive)
    assert "shadow_analysis" in result.document.metadata
    assert "shadow_provider" in result.document.metadata
    # trace_metadata alone would NOT contain shadow data
    assert "shadow_analysis" not in result.trace_metadata


# -- CLI: shadow-report --


def _make_shadow_doc(
    *,
    priority: int = 3,
    shadow_priority: int = 9,
    sentiment: SentimentLabel = SentimentLabel.BEARISH,
    shadow_sentiment: str = "bullish",
) -> CanonicalDocument:
    doc = CanonicalDocument(
        url="https://example.com/test",
        title="Test Doc",
        priority_score=priority,
        sentiment_label=sentiment,
        metadata={
            "shadow_analysis": {
                "recommended_priority": shadow_priority,
                "sentiment_label": shadow_sentiment,
            },
            "shadow_provider": "shadow/kai-v1",
        },
    )
    return doc


# shadow-report CLI command was removed with the legacy shadow subsystem.


# -- Overlap detection + ensemble-race fixes --


@pytest.mark.asyncio
async def test_shadow_skipped_when_ensemble_winner_matches_shadow() -> None:
    """If the ensemble falls back to the same provider configured as shadow,
    the shadow call must be skipped (no quota-duplicated request)."""
    from app.analysis.ensemble.provider import EnsembleProvider

    openai = _mock_provider("openai", sentiment=SentimentLabel.BULLISH)
    openai.analyze = AsyncMock(side_effect=RuntimeError("openai down"))

    gemini_primary = _mock_provider("gemini", sentiment=SentimentLabel.BULLISH)
    gemini_shadow = _mock_provider("gemini", sentiment=SentimentLabel.BEARISH)

    ensemble = EnsembleProvider(providers=[openai, gemini_primary])

    pipeline = AnalysisPipeline(
        _empty_engine(), ensemble, shadow_provider=gemini_shadow
    )
    result = await pipeline.run(_make_doc())

    # Primary ensemble resolved to gemini; shadow (also gemini) must NOT be called.
    assert gemini_shadow.analyze.await_count == 0
    assert result.shadow_llm_output is None
    assert result.shadow_provider_name is None


@pytest.mark.asyncio
async def test_shadow_called_serially_when_ensemble_winner_is_not_shadow() -> None:
    """If the ensemble winner is a DIFFERENT provider than the shadow, the
    shadow is still called (serially) and its result captured."""
    from app.analysis.ensemble.provider import EnsembleProvider

    openai_primary = _mock_provider("openai", sentiment=SentimentLabel.BULLISH)
    gemini_in_chain = _mock_provider("gemini", sentiment=SentimentLabel.NEUTRAL)
    gemini_shadow = _mock_provider("gemini", sentiment=SentimentLabel.BEARISH)

    ensemble = EnsembleProvider(providers=[openai_primary, gemini_in_chain])

    pipeline = AnalysisPipeline(
        _empty_engine(), ensemble, shadow_provider=gemini_shadow
    )
    result = await pipeline.run(_make_doc())

    # openai wins → gemini shadow still runs (overlap existed but didn't realize)
    assert gemini_shadow.analyze.await_count == 1
    assert result.shadow_llm_output is not None
    assert result.shadow_llm_output.sentiment_label == SentimentLabel.BEARISH


@pytest.mark.asyncio
async def test_overlap_detection_accepts_tuple_chain() -> None:
    """provider_chain as a tuple (not list) must still trigger overlap detection."""

    class TupleChainEnsemble:
        provider_name = "ensemble(openai,gemini)"
        model = "gemini"
        provider_chain = ("openai", "gemini")  # tuple, not list

        def __init__(self, inner: AsyncMock) -> None:
            self._inner = inner
            self.active_provider_name = "gemini"

        async def analyze(
            self, title: str, text: str, context: dict | None = None
        ) -> LLMAnalysisOutput:
            result = await self._inner.analyze(title, text, context)
            result.provider_used = "gemini"
            return result

    inner = _mock_provider("gemini", sentiment=SentimentLabel.BULLISH)
    ensemble = TupleChainEnsemble(inner)
    gemini_shadow = _mock_provider("gemini", sentiment=SentimentLabel.BEARISH)

    pipeline = AnalysisPipeline(
        _empty_engine(), ensemble, shadow_provider=gemini_shadow  # type: ignore[arg-type]
    )
    result = await pipeline.run(_make_doc())

    assert gemini_shadow.analyze.await_count == 0
    assert result.shadow_llm_output is None


@pytest.mark.asyncio
async def test_shadow_output_preserved_on_primary_exception() -> None:
    """When primary raises after shadow task started, shadow output must be
    captured into the PipelineResult (not silently dropped)."""
    primary = _mock_provider("openai", sentiment=SentimentLabel.BULLISH)
    primary.analyze = AsyncMock(side_effect=RuntimeError("primary blew up"))

    shadow = _mock_provider("anthropic", sentiment=SentimentLabel.BEARISH)

    pipeline = AnalysisPipeline(_empty_engine(), primary, shadow_provider=shadow)
    result = await pipeline.run(_make_doc())

    # Primary failed → fallback analysis_result, but shadow output survives
    assert result.analysis_result is not None
    assert result.shadow_llm_output is not None
    assert result.shadow_llm_output.sentiment_label == SentimentLabel.BEARISH
    assert result.shadow_provider_name == "anthropic"


def test_overlap_detection_flags_redundant_shadow() -> None:
    """When shadow is in the ensemble chain, _shadow_overlaps_ensemble() is True.

    This is the condition that triggers the CLAUDE.md §6 red-team warning at
    construction time and gates the runtime shadow-skip logic.
    """
    from app.analysis.ensemble.provider import EnsembleProvider

    p1 = _mock_provider("openai")
    p2 = _mock_provider("gemini")
    ensemble = EnsembleProvider(providers=[p1, p2])
    shadow_gemini = _mock_provider("gemini")

    pipeline = AnalysisPipeline(
        _empty_engine(), ensemble, shadow_provider=shadow_gemini
    )
    assert pipeline._shadow_overlaps_ensemble() is True


def test_overlap_detection_false_for_distinct_shadow() -> None:
    """A shadow that is NOT in the ensemble chain must not flag as overlap."""
    from app.analysis.ensemble.provider import EnsembleProvider

    p1 = _mock_provider("openai")
    p2 = _mock_provider("gemini")
    ensemble = EnsembleProvider(providers=[p1, p2])
    shadow_anthropic = _mock_provider("anthropic")

    pipeline = AnalysisPipeline(
        _empty_engine(), ensemble, shadow_provider=shadow_anthropic
    )
    assert pipeline._shadow_overlaps_ensemble() is False
