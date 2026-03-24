"""PH5C: Tests for pre-LLM stub/empty document filter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.analysis.pipeline import _STUB_CONTENT_THRESHOLD, AnalysisPipeline
from app.core.enums import AnalysisSource


def _make_doc(*, raw_text: str = "", title: str = "Test Title"):
    """Create a minimal CanonicalDocument-like object for pipeline testing."""
    from app.core.domain.document import CanonicalDocument

    return CanonicalDocument(
        id=uuid4(),
        url=f"https://example.com/{uuid4()}",
        title=title,
        raw_text=raw_text,
    )


def _make_pipeline(*, provider: MagicMock | None = None) -> AnalysisPipeline:
    """Create a pipeline with a mock keyword engine and optional mock provider."""
    keyword_engine = MagicMock()
    keyword_engine.match.return_value = []
    keyword_engine.match_tickers.return_value = []
    return AnalysisPipeline(
        keyword_engine=keyword_engine,
        provider=provider,
        run_llm=True,
    )


class TestStubDocumentFilter:
    """PH5C: Documents with body content <= threshold skip LLM."""

    @pytest.mark.asyncio
    async def test_stub_document_skips_llm(self):
        """8-byte body -> fallback, no LLM call, stub_document in tags."""
        provider = MagicMock()
        provider.provider_name = "openai"
        provider.analyze = AsyncMock()

        pipeline = _make_pipeline(provider=provider)
        doc = _make_doc(raw_text="Comments")  # 8 bytes, like PH5B proxy docs

        result = await pipeline.run(doc)

        # LLM should NOT be called
        provider.analyze.assert_not_called()

        # Should have fallback result
        assert result.analysis_result is not None
        assert result.analysis_result.analysis_source == AnalysisSource.RULE
        assert "stub_document" in result.analysis_result.tags
        assert "stub_document" in result.analysis_result.explanation_short.lower()

    @pytest.mark.asyncio
    async def test_normal_document_uses_llm(self):
        """500-byte body -> LLM is called."""
        from app.analysis.base.interfaces import LLMAnalysisOutput
        from app.core.enums import MarketScope, SentimentLabel

        llm_output = LLMAnalysisOutput(
            sentiment_label=SentimentLabel.NEUTRAL,
            sentiment_score=0.0,
            relevance_score=0.5,
            impact_score=0.3,
            confidence_score=0.7,
            novelty_score=0.5,
            market_scope=MarketScope.CRYPTO,
            affected_assets=["BTC"],
            affected_sectors=[],
            event_type=None,
            short_reasoning="test",
            long_reasoning="test long",
            actionable=False,
            tags=["crypto"],
            spam_probability=0.0,
        )

        provider = MagicMock()
        provider.provider_name = "openai"
        provider.model = "gpt-4"
        provider.analyze = AsyncMock(return_value=llm_output)

        pipeline = _make_pipeline(provider=provider)
        doc = _make_doc(raw_text="A" * 500)  # Well above threshold

        result = await pipeline.run(doc)

        # LLM SHOULD be called
        provider.analyze.assert_called_once()
        assert result.llm_output is not None

    @pytest.mark.asyncio
    async def test_stub_threshold_boundary(self):
        """Exactly 50 bytes -> fallback. 51 bytes -> LLM called."""
        from app.analysis.base.interfaces import LLMAnalysisOutput
        from app.core.enums import MarketScope, SentimentLabel

        llm_output = LLMAnalysisOutput(
            sentiment_label=SentimentLabel.NEUTRAL,
            sentiment_score=0.0,
            relevance_score=0.5,
            impact_score=0.3,
            confidence_score=0.7,
            novelty_score=0.5,
            market_scope=MarketScope.CRYPTO,
            affected_assets=[],
            affected_sectors=[],
            event_type=None,
            short_reasoning="test",
            long_reasoning="test long",
            actionable=False,
            tags=[],
            spam_probability=0.0,
        )

        # Exactly at threshold -> fallback
        provider_at = MagicMock()
        provider_at.provider_name = "openai"
        provider_at.analyze = AsyncMock()
        pipeline_at = _make_pipeline(provider=provider_at)
        doc_at = _make_doc(raw_text="A" * _STUB_CONTENT_THRESHOLD)

        result_at = await pipeline_at.run(doc_at)
        provider_at.analyze.assert_not_called()
        assert result_at.analysis_result is not None
        assert result_at.analysis_result.analysis_source == AnalysisSource.RULE

        # One byte above threshold -> LLM
        provider_above = MagicMock()
        provider_above.provider_name = "openai"
        provider_above.model = "gpt-4"
        provider_above.analyze = AsyncMock(return_value=llm_output)
        pipeline_above = _make_pipeline(provider=provider_above)
        doc_above = _make_doc(raw_text="A" * (_STUB_CONTENT_THRESHOLD + 1))

        await pipeline_above.run(doc_above)
        provider_above.analyze.assert_called_once()
