"""Tests for the RSS Feed Adapter."""

from __future__ import annotations

import pytest

from app.core.enums import SourceStatus, SourceType
from app.ingestion.rss.adapter import RSSFeedAdapter, _strip_html


class TestHTMLStripping:
    def test_strips_tags(self) -> None:
        assert _strip_html("<p>Hello <strong>World</strong></p>") == "Hello World"

    def test_handles_empty(self) -> None:
        assert _strip_html("") == ""

    def test_plain_text_unchanged(self) -> None:
        assert _strip_html("Plain text") == "Plain text"


class TestRSSAdapterMetadata:
    def test_metadata_fields(self) -> None:
        adapter = RSSFeedAdapter(
            source_id="test_rss", feed_url="https://example.com/feed.rss",
            source_name="Test Feed", language="en", categories=["crypto"],
        )
        meta = adapter.metadata
        assert meta.source_id == "test_rss"
        assert meta.source_type == SourceType.RSS_FEED
        assert meta.status == SourceStatus.ACTIVE
        assert "crypto" in meta.categories

    def test_to_dict(self) -> None:
        adapter = RSSFeedAdapter(
            source_id="rss_test", feed_url="https://feeds.example.com/rss", source_name="Example",
        )
        d = adapter.metadata.to_dict()
        assert d["source_type"] == "rss_feed"


class TestRSSNormalization:
    def test_normalize_empty_feed(self) -> None:
        adapter = RSSFeedAdapter(source_id="t", feed_url="https://ex.com", source_name="T")
        docs = adapter._normalize("<rss version='2.0'><channel></channel></rss>")
        assert isinstance(docs, list)
        assert len(docs) == 0

    def test_normalize_basic_feed(self) -> None:
        feed_xml = """<?xml version="1.0"?>
        <rss version="2.0"><channel>
          <item>
            <title>Bitcoin hits ATH</title>
            <link>https://example.com/btc-ath</link>
            <description>Bitcoin reached a new all-time high.</description>
          </item>
        </channel></rss>"""
        adapter = RSSFeedAdapter(
            source_id="test", feed_url="https://ex.com/feed", source_name="Test", categories=["crypto"],
        )
        docs = adapter._normalize(feed_xml)
        assert len(docs) == 1
        assert docs[0].title == "Bitcoin hits ATH"
        assert docs[0].source_id == "test"
        assert "crypto" in docs[0].categories
