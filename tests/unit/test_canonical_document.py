"""Tests for CanonicalDocument, EntityMention, AnalysisResult."""

from uuid import UUID

import pytest
from pydantic import ValidationError

from app.core.domain.document import (
    AnalysisResult,
    CanonicalDocument,
    EntityMention,
    PodcastEpisodeMeta,
    YouTubeVideoMeta,
)
from app.core.enums import (
    DocumentType,
    MarketScope,
    SentimentLabel,
    SourceType,
)


class TestEntityMention:
    def test_minimal(self):
        e = EntityMention(name="Satoshi Nakamoto", entity_type="person")
        assert e.confidence == 1.0
        assert e.source == "rule"
        assert e.normalized_name is None

    def test_full(self):
        e = EntityMention(
            name="BTC",
            entity_type="crypto_asset",
            normalized_name="bitcoin",
            context="BTC surged 10% overnight",
            confidence=0.95,
            source="llm",
            url="https://coingecko.com/en/coins/bitcoin",
        )
        assert e.normalized_name == "bitcoin"
        assert e.confidence == 0.95

    def test_confidence_bounds(self):
        with pytest.raises(ValidationError):
            EntityMention(name="X", entity_type="person", confidence=1.5)
        with pytest.raises(ValidationError):
            EntityMention(name="X", entity_type="person", confidence=-0.1)


class TestCanonicalDocument:
    def test_minimal_creation(self):
        doc = CanonicalDocument(url="https://coindesk.com/article/1", title="BTC Hits ATH")
        assert isinstance(doc.id, UUID)
        assert doc.document_type == DocumentType.UNKNOWN
        assert doc.market_scope == MarketScope.UNKNOWN
        assert doc.is_duplicate is False
        assert doc.is_analyzed is False

    def test_content_hash_auto_computed(self):
        doc = CanonicalDocument(url="https://example.com/a", title="Test")
        assert doc.content_hash is not None
        assert len(doc.content_hash) == 64  # SHA-256 hex

    def test_same_content_same_hash(self):
        doc1 = CanonicalDocument(url="https://example.com/a", title="Test", raw_text="hello")
        doc2 = CanonicalDocument(url="https://example.com/a", title="Test", raw_text="hello")
        assert doc1.content_hash == doc2.content_hash

    def test_different_content_different_hash(self):
        doc1 = CanonicalDocument(url="https://example.com/a", title="Test A")
        doc2 = CanonicalDocument(url="https://example.com/b", title="Test B")
        assert doc1.content_hash != doc2.content_hash

    def test_manual_hash_not_overwritten(self):
        custom_hash = "a" * 64
        doc = CanonicalDocument(url="https://example.com/a", title="Test", content_hash=custom_hash)
        assert doc.content_hash == custom_hash

    def test_word_count_from_raw_text(self):
        doc = CanonicalDocument(
            url="https://example.com/a", title="T", raw_text="one two three four five"
        )
        assert doc.word_count == 5

    def test_word_count_prefers_cleaned_text(self):
        doc = CanonicalDocument(
            url="https://example.com/a",
            title="T",
            raw_text="one two three",
            cleaned_text="one two",
        )
        assert doc.word_count == 2

    def test_word_count_empty(self):
        doc = CanonicalDocument(url="https://example.com/a", title="T")
        assert doc.word_count == 0

    def test_entity_mentions(self):
        doc = CanonicalDocument(
            url="https://example.com/a",
            title="T",
            entity_mentions=[
                EntityMention(name="Bitcoin", entity_type="crypto_asset"),
                EntityMention(name="Elon Musk", entity_type="person"),
            ],
        )
        assert len(doc.entity_mentions) == 2

    def test_youtube_meta(self):
        doc = CanonicalDocument(
            url="https://youtube.com/watch?v=abc",
            title="Video",
            source_type=SourceType.YOUTUBE_CHANNEL,
            document_type=DocumentType.YOUTUBE_VIDEO,
            youtube_meta=YouTubeVideoMeta(video_id="abc", view_count=1000),
        )
        assert doc.youtube_meta is not None
        assert doc.youtube_meta.view_count == 1000

    def test_podcast_meta(self):
        doc = CanonicalDocument(
            url="https://example.com/ep/1",
            title="Episode 1",
            source_type=SourceType.PODCAST_FEED,
            document_type=DocumentType.PODCAST_EPISODE,
            podcast_meta=PodcastEpisodeMeta(episode_number=42, duration_seconds=3600),
        )
        assert doc.podcast_meta is not None
        assert doc.podcast_meta.episode_number == 42

    def test_score_bounds_valid(self):
        doc = CanonicalDocument(
            url="https://example.com/a",
            title="T",
            relevance_score=0.8,
            impact_score=0.5,
            sentiment_score=-0.3,
        )
        assert doc.relevance_score == 0.8

    def test_score_bounds_invalid(self):
        with pytest.raises(ValidationError):
            CanonicalDocument(url="https://example.com/a", title="T", relevance_score=1.5)


class TestAnalysisResult:
    def test_minimal(self):
        from uuid import uuid4

        doc_id = uuid4()
        result = AnalysisResult(
            document_id=str(doc_id),
            sentiment_label=SentimentLabel.BULLISH,
            sentiment_score=0.8,
            relevance_score=0.9,
            impact_score=0.7,
            confidence_score=0.85,
            novelty_score=0.6,
            explanation_short="BTC hit ATH.",
            explanation_long="Bitcoin reached a new all-time high driven by ETF inflows.",
        )
        assert result.document_id == str(doc_id)
        assert result.actionable is False
        assert result.sentiment_label == SentimentLabel.BULLISH

    def test_score_ranges(self):
        from uuid import uuid4

        with pytest.raises(ValidationError):
            AnalysisResult(
                document_id=str(uuid4()),
                sentiment_label=SentimentLabel.NEUTRAL,
                sentiment_score=2.0,  # invalid
                relevance_score=0.5,
                impact_score=0.5,
                confidence_score=0.5,
                novelty_score=0.5,
                explanation_short="Test",
                explanation_long="Test long",
            )

    def test_priority_bounds(self):
        from uuid import uuid4

        with pytest.raises(ValidationError):
            AnalysisResult(
                document_id=str(uuid4()),
                sentiment_label=SentimentLabel.NEUTRAL,
                sentiment_score=0.0,
                relevance_score=0.5,
                impact_score=0.5,
                confidence_score=0.5,
                novelty_score=0.5,
                explanation_short="Test",
                explanation_long="Test long",
                recommended_priority=11,  # invalid
            )
