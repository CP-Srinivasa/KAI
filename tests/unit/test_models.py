import pytest
from pydantic import ValidationError

from app.analysis.base.interfaces import LLMAnalysisOutput
from app.core.domain.document import CanonicalDocument, QuerySpec
from app.core.enums import MarketScope, SentimentLabel, SortBy, SourceStatus, SourceType


def test_canonical_document_minimal():
    doc = CanonicalDocument(url="https://example.com", title="Test")
    assert doc.url == "https://example.com"
    assert doc.title == "Test"
    assert doc.tags == []
    assert doc.entities == []
    assert doc.id is not None


def test_canonical_document_hash():
    doc = CanonicalDocument(url="https://example.com", title="Test", raw_text="Hello")
    h = doc.compute_hash()
    assert isinstance(h, str)
    assert len(h) == 64  # sha256 hex


def test_canonical_document_hash_deterministic():
    doc1 = CanonicalDocument(url="https://example.com", title="Test", raw_text="Hello")
    doc2 = CanonicalDocument(url="https://example.com", title="Test", raw_text="Hello")
    assert doc1.compute_hash() == doc2.compute_hash()


def test_canonical_document_hash_differs_on_content():
    doc1 = CanonicalDocument(url="https://example.com", title="Test", raw_text="Hello")
    doc2 = CanonicalDocument(url="https://example.com", title="Test", raw_text="World")
    assert doc1.compute_hash() != doc2.compute_hash()


def test_query_spec_defaults():
    spec = QuerySpec()
    assert spec.limit == 50
    assert spec.offset == 0
    assert spec.deduplicate is True
    assert spec.sort_by == SortBy.PUBLISHED_AT


def test_query_spec_limit_bounds():
    with pytest.raises(ValidationError):
        QuerySpec(limit=0)
    with pytest.raises(ValidationError):
        QuerySpec(limit=9999)


def test_source_type_values():
    assert SourceType.RSS_FEED == "rss_feed"
    assert SourceType.YOUTUBE_CHANNEL == "youtube_channel"
    assert SourceType.UNRESOLVED_SOURCE == "unresolved_source"
    assert SourceType.PODCAST_FEED == "podcast_feed"
    assert SourceType.WEBSITE == "website"


def test_source_status_values():
    assert SourceStatus.ACTIVE == "active"
    assert SourceStatus.REQUIRES_API == "requires_api"
    assert SourceStatus.UNRESOLVED == "unresolved"
    assert SourceStatus.DISABLED == "disabled"


def test_sentiment_label_values():
    assert SentimentLabel.BULLISH == "bullish"
    assert SentimentLabel.BEARISH == "bearish"
    assert SentimentLabel.NEUTRAL == "neutral"
    assert SentimentLabel.MIXED == "mixed"


def test_llm_analysis_output_schema():
    output = LLMAnalysisOutput(
        sentiment_label=SentimentLabel.BULLISH,
        sentiment_score=0.8,
        relevance_score=0.9,
        impact_score=0.7,
        confidence_score=0.85,
        novelty_score=0.6,
        spam_probability=0.05,
    )
    assert output.sentiment_label == SentimentLabel.BULLISH
    assert output.market_scope == MarketScope.UNKNOWN
    assert output.actionable is False
    assert output.recommended_priority == 5


def test_llm_analysis_output_score_bounds():
    with pytest.raises(ValidationError):
        LLMAnalysisOutput(
            sentiment_label=SentimentLabel.NEUTRAL,
            sentiment_score=2.0,  # invalid: > 1.0
            relevance_score=0.5,
            impact_score=0.5,
            confidence_score=0.5,
            novelty_score=0.5,
            spam_probability=0.5,
        )
