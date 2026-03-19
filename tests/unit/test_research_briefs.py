"""Tests for Research Briefs module."""

from app.core.enums import SentimentLabel
from app.research.briefs import ResearchBriefBuilder
from tests.unit.factories import make_document


def test_research_brief_builder_empty():
    builder = ResearchBriefBuilder("DeFi")
    brief = builder.build([])
    assert brief.cluster_name == "DeFi"
    assert brief.document_count == 0
    assert brief.average_priority == 0.0
    assert brief.overall_sentiment == "neutral"
    assert len(brief.top_actionable_signals) == 0
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
        ),
        make_document(
            title="Regular DeFi News",
            is_analyzed=True,
            priority_score=5,
            sentiment_label=SentimentLabel.BULLISH,
            impact_score=0.4,
            summary="Protocol update.",
        ),
        make_document(
            title="Unanalyzed Doc",
            is_analyzed=False,  # Should be ignored
        ),
    ]
    builder = ResearchBriefBuilder("DeFi")
    brief = builder.build(docs)
    assert brief.document_count == 2
    assert brief.average_priority == 7.0  # (9 + 5) / 2
    assert len(brief.top_actionable_signals) == 1
    assert brief.top_actionable_signals[0].title == "High Priority DeFi Hack"
    assert len(brief.key_documents) == 1
    assert brief.key_documents[0].title == "Regular DeFi News"


def test_research_brief_to_markdown():
    docs = [
        make_document(
            title="Test Actionable",
            is_analyzed=True,
            priority_score=10,
            sentiment_label=SentimentLabel.BULLISH,
            summary="Markdown Summary",
            url="http://example.com/1",
        )
    ]
    builder = ResearchBriefBuilder("Test Cluster")
    brief = builder.build(docs)
    md = brief.to_markdown()
    assert "# Research Brief: Test Cluster" in md
    assert "**Average Priority:** 10.00 / 10" in md
    assert "### [Test Actionable](http://example.com/1)" in md
    assert "> Markdown Summary" in md
    assert "🟢 Bullish" in md


def test_research_brief_to_json():
    docs = []
    builder = ResearchBriefBuilder("JSON Test")
    brief = builder.build(docs)
    data = brief.to_json_dict()
    assert data["cluster_name"] == "JSON Test"
    assert data["document_count"] == 0
    assert isinstance(data["generated_at"], str)  # Pydantic mode='json' converts datetime to string
