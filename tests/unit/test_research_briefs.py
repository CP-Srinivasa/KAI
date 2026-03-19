"""Tests for Research Briefs module."""

import pytest

from app.analysis.keywords.engine import KeywordEngine
from app.analysis.keywords.watchlist import WatchlistEntry
from app.analysis.pipeline import AnalysisPipeline
from app.core.enums import SentimentLabel
from app.research.briefs import ResearchBriefBuilder
from tests.unit.factories import make_document


def test_research_brief_builder_empty():
    builder = ResearchBriefBuilder("DeFi")
    brief = builder.build([])
    assert brief.cluster_name == "DeFi"
    assert brief.title == "Research Brief: DeFi"
    assert brief.document_count == 0
    assert brief.average_priority == 0.0
    assert brief.overall_sentiment == "neutral"
    assert brief.summary == "No analyzed documents available for this brief."
    assert len(brief.top_actionable_signals) == 0
    assert len(brief.top_documents) == 0
    assert len(brief.key_documents) == 0


def test_research_brief_builder_with_valid_documents():
    docs = [
        make_document(
            title="High Priority DeFi Hack",
            is_analyzed=True,
            priority_score=9,
            sentiment_label=SentimentLabel.BEARISH,
            impact_score=0.9,
            summary="Major hack.",
            tickers=["ETH"],
            crypto_assets=["ETH"],
            entities=["Uniswap"],
        ),
        make_document(
            title="Regular DeFi News",
            is_analyzed=True,
            priority_score=5,
            sentiment_label=SentimentLabel.BULLISH,
            impact_score=0.4,
            summary="Protocol update.",
            tickers=["ETH"],
            crypto_assets=["ETH"],
            entities=["Aave"],
        ),
        make_document(
            title="Unanalyzed Doc",
            is_analyzed=False,
        ),
    ]
    builder = ResearchBriefBuilder("DeFi")
    brief = builder.build(docs)
    assert brief.document_count == 2
    assert brief.average_priority == 7.0
    assert brief.summary.startswith("2 analyzed documents")
    assert len(brief.top_actionable_signals) == 1
    assert brief.top_actionable_signals[0].title == "High Priority DeFi Hack"
    assert brief.top_actionable_signals[0].analysis_source in ("external_llm", "rule", "internal")
    assert len(brief.top_documents) == 2
    assert brief.top_documents[0].title == "High Priority DeFi Hack"
    assert len(brief.key_documents) == 1
    assert brief.key_documents[0].title == "Regular DeFi News"
    assert brief.top_assets[0].name == "ETH"
    assert brief.top_assets[0].count == 4
    assert {facet.name for facet in brief.top_entities} == {"Aave", "Uniswap"}


def test_research_brief_builder_handles_missing_priority_safely():
    docs = [
        make_document(
            title="Analyzed without priority",
            is_analyzed=True,
            priority_score=None,
            summary=None,
            sentiment_label=None,
        )
    ]
    builder = ResearchBriefBuilder("Fallback")
    brief = builder.build(docs)

    assert brief.document_count == 1
    assert brief.average_priority == 0.0
    assert brief.top_documents[0].priority_score == 0
    assert brief.top_documents[0].summary == "Analyzed without priority"
    assert brief.top_documents[0].sentiment_label == "neutral"
    assert brief.top_documents[0].analysis_source == "rule"


def test_research_brief_to_markdown():
    docs = [
        make_document(
            title="Test Actionable",
            is_analyzed=True,
            priority_score=10,
            sentiment_label=SentimentLabel.BULLISH,
            summary="Markdown Summary",
            url="http://example.com/1",
            tickers=["BTC"],
            entities=["BlackRock"],
        )
    ]
    builder = ResearchBriefBuilder("Test Cluster")
    brief = builder.build(docs)
    md = brief.to_markdown()
    assert "# Research Brief: Test Cluster" in md
    assert "Markdown Summary" in md
    assert "## Top Assets" in md
    assert "**BTC** (1)" in md
    assert "## Top Entities" in md
    assert "**BlackRock** (1)" in md
    assert "### [Test Actionable](http://example.com/1)" in md
    assert "🟢 Bullish" in md


def test_research_brief_to_json():
    docs = []
    builder = ResearchBriefBuilder("JSON Test")
    brief = builder.build(docs)
    data = brief.to_json_dict()
    assert data["cluster_name"] == "JSON Test"
    assert data["title"] == "Research Brief: JSON Test"
    assert data["document_count"] == 0
    assert isinstance(data["generated_at"], str)


@pytest.mark.asyncio
async def test_research_brief_builder_with_fallback_analyzed_document():
    engine = KeywordEngine(
        keywords=frozenset({"halving", "regulation"}),
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
    pipeline = AnalysisPipeline(keyword_engine=engine, provider=None)
    doc = make_document(
        title="Bitcoin regulation update",
        raw_text="BTC regulation and halving continue to drive discussion.",
    )

    result = await pipeline.run(doc)
    assert result.analysis_result is not None

    result.apply_to_document()
    doc.is_analyzed = True

    brief = ResearchBriefBuilder("Fallback").build([doc])

    assert brief.document_count == 1
    assert brief.summary.startswith("1 analyzed documents")
    assert brief.top_documents[0].title == "Bitcoin regulation update"
    assert brief.top_assets[0].name == "BTC"
