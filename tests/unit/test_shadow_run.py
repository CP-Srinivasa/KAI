"""Tests for AnalysisPipeline shadow run behaviour (I-51–I-57).

Sprint 10 — Companion Shadow Run.
Contract reference: docs/sprint10_shadow_run_contract.md
Invariants: I-51–I-57.

Tests cover:
- Shadow output stored in document.metadata (live inline path)
- Primary result unchanged when shadow runs
- Shadow failure non-blocking
- No shadow fields when shadow_provider=None
- shadow-report CLI: divergence display and empty case
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

# ── helpers ───────────────────────────────────────────────────────────────────


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


# ── I-56 + I-57: live shadow stores output in document.metadata ───────────────


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


# ── I-56: shadow failure is non-blocking ──────────────────────────────────────


@pytest.mark.asyncio
async def test_shadow_failure_is_non_blocking() -> None:
    """Shadow exception leaves primary result intact (I-52, I-56)."""
    primary = _mock_provider("openai", sentiment=SentimentLabel.BULLISH, priority=7)
    shadow = AsyncMock()
    shadow.provider_name = "companion"
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


# ── I-56: no shadow run when shadow_provider is None ──────────────────────────


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


# ── I-57: shadow persistence fix — document.metadata reaches DB ──────────────


@pytest.mark.asyncio
async def test_shadow_data_present_in_document_metadata_for_db_write() -> None:
    """After apply_to_document(), shadow data is in doc.metadata ready for update_analysis.

    Verifies I-57: update_analysis should receive res.document.metadata (not trace_metadata)
    so shadow data reaches the DB. This test confirms the data is IN doc.metadata post-apply.
    """
    primary = _mock_provider("openai")
    shadow = _mock_provider("companion", "kai-v1")

    pipeline = AnalysisPipeline(_empty_engine(), primary, shadow_provider=shadow)
    doc = _make_doc()
    result = await pipeline.run(doc)
    result.apply_to_document()

    # Both shadow keys must be in doc.metadata (what update_analysis would receive)
    assert "shadow_analysis" in result.document.metadata
    assert "shadow_provider" in result.document.metadata
    # trace_metadata alone would NOT contain shadow data
    assert "shadow_analysis" not in result.trace_metadata


# ── CLI: shadow-report ────────────────────────────────────────────────────────


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
            "shadow_provider": "companion/kai-v1",
        },
    )
    return doc


# shadow-report CLI command was removed with companion-ML subsystem.
