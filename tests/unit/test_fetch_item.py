"""Tests for FetchItem and normalize_fetch_item()."""

from datetime import UTC, datetime

from app.core.domain.document import CanonicalDocument
from app.core.enums import SourceType
from app.ingestion.base.interfaces import FetchItem, normalize_fetch_item

# ── construction ──────────────────────────────────────────────────────────────


def test_fetch_item_requires_only_url():
    item = FetchItem(url="https://example.com/article")
    assert item.url == "https://example.com/article"
    assert item.external_id is None
    assert item.title is None
    assert item.content is None
    assert item.published_at is None
    assert item.metadata == {}


def test_fetch_item_with_all_fields():
    pub = datetime(2026, 3, 17, 12, 0, tzinfo=UTC)
    item = FetchItem(
        url="https://example.com/btc",
        external_id="guid-123",
        title="Bitcoin Hits ATH",
        content="Bitcoin reached a new all-time high today.",
        published_at=pub,
        metadata={"image_url": "https://example.com/img.png", "author": "Alice"},
    )
    assert item.external_id == "guid-123"
    assert item.title == "Bitcoin Hits ATH"
    assert item.content == "Bitcoin reached a new all-time high today."
    assert item.published_at == pub
    assert item.metadata["author"] == "Alice"


def test_fetch_item_metadata_defaults_to_empty_dict():
    a = FetchItem(url="https://a.com")
    b = FetchItem(url="https://b.com")
    a.metadata["key"] = "value"
    assert "key" not in b.metadata  # separate instances


# ── contract: no analysis fields ──────────────────────────────────────────────


def test_fetch_item_has_no_analysis_fields():
    item = FetchItem(url="https://example.com")
    analysis_fields = [
        "sentiment_label", "sentiment_score", "relevance_score",
        "impact_score", "novelty_score", "spam_probability",
        "priority_score", "credibility_score", "tickers",
        "entity_mentions", "categories", "tags",
    ]
    for field_name in analysis_fields:
        assert not hasattr(item, field_name), f"FetchItem must not have field: {field_name}"


# ── contract: no persistence state ───────────────────────────────────────────


def test_fetch_item_has_no_persistence_state():
    item = FetchItem(url="https://example.com")
    persistence_fields = [
        "status", "is_analyzed", "is_duplicate",
        "content_hash", "id", "source_id", "source_name", "source_type",
    ]
    for field_name in persistence_fields:
        assert not hasattr(item, field_name), f"FetchItem must not have field: {field_name}"


# ── normalize_fetch_item ──────────────────────────────────────────────────────


def test_normalize_returns_canonical_document():
    item = FetchItem(
        url="https://example.com/btc",
        title="Bitcoin Hits ATH",
        content="Bitcoin reached a new all-time high.",
        external_id="guid-456",
    )
    doc = normalize_fetch_item(
        item,
        source_id="src-rss-1",
        source_name="CoinTelegraph RSS",
        source_type=SourceType.RSS_FEED,
    )
    assert isinstance(doc, CanonicalDocument)
    assert doc.url == "https://example.com/btc"
    assert doc.title == "Bitcoin Hits ATH"
    assert doc.raw_text == "Bitcoin reached a new all-time high."
    assert doc.external_id == "guid-456"
    assert doc.source_id == "src-rss-1"
    assert doc.source_name == "CoinTelegraph RSS"
    assert doc.source_type == SourceType.RSS_FEED


def test_normalize_preserves_published_at():
    pub = datetime(2026, 3, 17, 8, 0, tzinfo=UTC)
    item = FetchItem(url="https://example.com/article", published_at=pub)
    doc = normalize_fetch_item(
        item, source_id="s", source_name="S", source_type=SourceType.RSS_FEED
    )
    assert doc.published_at == pub


def test_normalize_preserves_metadata():
    item = FetchItem(
        url="https://example.com/article",
        metadata={"image_url": "https://example.com/img.png"},
    )
    doc = normalize_fetch_item(
        item, source_id="s", source_name="S", source_type=SourceType.RSS_FEED
    )
    assert doc.metadata["image_url"] == "https://example.com/img.png"


def test_normalize_sets_empty_title_when_none():
    item = FetchItem(url="https://example.com/article", title=None)
    doc = normalize_fetch_item(
        item, source_id="s", source_name="S", source_type=SourceType.RSS_FEED
    )
    assert doc.title == ""


def test_normalize_does_not_set_content_hash():
    """content_hash must be auto-computed by CanonicalDocument, not by normalize."""
    item = FetchItem(url="https://example.com/article", title="Test", content="body")
    doc = normalize_fetch_item(
        item, source_id="s", source_name="S", source_type=SourceType.RSS_FEED
    )
    # content_hash is auto-computed — it should be set and be a hex string
    assert doc.content_hash is not None
    assert len(doc.content_hash) == 64  # SHA-256 hex


def test_normalize_produces_no_analysis_fields():
    item = FetchItem(url="https://example.com/article", title="Test")
    doc = normalize_fetch_item(
        item, source_id="s", source_name="S", source_type=SourceType.RSS_FEED
    )
    assert doc.sentiment_label is None
    assert doc.relevance_score is None
    assert doc.priority_score is None
    assert doc.spam_probability is None
    assert not doc.is_analyzed


def test_normalize_produces_pending_status():
    from app.core.enums import DocumentStatus

    item = FetchItem(url="https://example.com/article", title="Test")
    doc = normalize_fetch_item(
        item, source_id="s", source_name="S", source_type=SourceType.RSS_FEED
    )
    assert doc.status == DocumentStatus.PENDING
    assert not doc.is_duplicate
