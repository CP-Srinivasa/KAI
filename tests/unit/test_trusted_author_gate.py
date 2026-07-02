"""Tests for D-174 Phase I (2Y) trusted-author gate bypass."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.analysis.pipeline import (
    AnalysisPipeline,
    _extract_author_handle,
    load_trusted_social_handles,
)
from app.core.domain.document import CanonicalDocument
from app.core.enums import DocumentType, SourceType


def _make_doc(
    author: str,
    text: str,
    source_type: SourceType = SourceType.SOCIAL_API,
) -> CanonicalDocument:
    return CanonicalDocument(
        id=uuid4(),
        external_id="tid-1",
        source_id="twitter",
        source_name="Twitter",
        source_type=source_type,
        document_type=DocumentType.ARTICLE,
        provider="twitter",
        title=f"{author}: {text[:40]}",
        raw_text=text,
        author=author,
        url="https://x.com/x/status/1",
    )


def test_loader_reads_curated_watchlist(tmp_path: Path) -> None:
    monitor = tmp_path / "monitor"
    monitor.mkdir()
    (monitor / "social_accounts.txt").write_text(
        "# comment line\n"
        "twitter|@Saylor|Michael Saylor|bitcoin_maxi\n"
        "twitter|@ElonMusk|Elon Musk|influencer\n"
        "reddit|@someone|Someone|x\n"  # non-twitter must be ignored
        "\n",
        encoding="utf-8",
    )
    handles = load_trusted_social_handles(monitor)
    assert handles == frozenset({"saylor", "elonmusk"})


def test_loader_returns_empty_when_file_missing(tmp_path: Path) -> None:
    assert load_trusted_social_handles(tmp_path / "nope") == frozenset()


def test_extract_handle_variants() -> None:
    assert _extract_author_handle("@elonmusk (Elon Musk)") == "elonmusk"
    assert _extract_author_handle("@Saylor (Michael Saylor)") == "saylor"
    assert _extract_author_handle("no handle here") is None
    assert _extract_author_handle(None) is None


def test_is_trusted_matches_social_api_only() -> None:
    pipeline = AnalysisPipeline(
        keyword_engine=MagicMock(),
        trusted_social_handles=frozenset({"saylor"}),
    )
    tweet = _make_doc(author="@saylor (Michael Saylor)", text="BTC")
    rss = _make_doc(author="@saylor (Michael Saylor)", text="BTC", source_type=SourceType.RSS_FEED)
    other = _make_doc(author="@pleb", text="BTC")

    assert pipeline._is_trusted_social_author(tweet) is True
    assert pipeline._is_trusted_social_author(rss) is False
    assert pipeline._is_trusted_social_author(other) is False


@pytest.mark.asyncio
async def test_trusted_author_bypasses_stub_gate() -> None:
    """Short tweet from curated author must reach the LLM, not the fallback path."""
    from app.analysis.base.interfaces import LLMAnalysisOutput
    from app.core.enums import MarketScope, SentimentLabel

    llm_output = LLMAnalysisOutput(
        sentiment_label=SentimentLabel.BULLISH,
        sentiment_score=0.8,
        relevance_score=1.0,
        impact_score=0.9,
        confidence_score=0.85,
        novelty_score=0.5,
        spam_probability=0.02,
        market_scope=MarketScope.CRYPTO,
        affected_assets=["BTC"],
        affected_sectors=["Layer1"],
        short_reasoning="Saylor added BTC.",
        recommended_priority=6,
        actionable=True,
        tags=["bitcoin"],
    )
    provider = MagicMock()
    provider.provider_name = "mock"
    provider.model = "mock-1"
    provider.analyze = AsyncMock(return_value=llm_output)

    keyword_engine = MagicMock()
    keyword_engine.match.return_value = []
    keyword_engine.match_tickers.return_value = []

    pipeline = AnalysisPipeline(
        keyword_engine=keyword_engine,
        provider=provider,
        run_llm=True,
        trusted_social_handles=frozenset({"saylor"}),
    )

    # 12-char tweet body — well below the 50-char stub threshold
    doc = _make_doc(author="@saylor (Michael Saylor)", text="Added BTC.")
    result = await pipeline.run(doc)

    provider.analyze.assert_awaited_once()
    assert result.llm_output is not None
    assert result.llm_output.recommended_priority == 6
