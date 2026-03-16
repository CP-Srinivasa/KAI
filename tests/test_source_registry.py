"""Tests for app/ingestion/source_registry.py"""
from __future__ import annotations

import pytest

from app.core.enums import AuthMode, SourceStatus, SourceType
from app.ingestion.source_registry import (
    SourceEntry,
    SourceRegistry,
    build_registry,
)


def _make_entry(
    source_id: str = "test_source",
    source_type: SourceType = SourceType.RSS_FEED,
    status: SourceStatus = SourceStatus.ACTIVE,
) -> SourceEntry:
    return SourceEntry(
        source_id=source_id,
        source_name=source_id.replace("_", " ").title(),
        source_type=source_type,
        status=status,
        url=f"https://{source_id}.example.com/feed",
    )


class TestSourceEntry:
    def test_is_active(self) -> None:
        entry = _make_entry(status=SourceStatus.ACTIVE)
        assert entry.is_active is True

    def test_is_not_active(self) -> None:
        entry = _make_entry(status=SourceStatus.REQUIRES_API)
        assert entry.is_active is False

    def test_is_fetchable_only_when_active(self) -> None:
        active = _make_entry(status=SourceStatus.ACTIVE)
        requires_api = _make_entry(status=SourceStatus.REQUIRES_API)
        disabled = _make_entry(status=SourceStatus.DISABLED)

        assert active.is_fetchable is True
        assert requires_api.is_fetchable is False
        assert disabled.is_fetchable is False

    def test_to_dict_contains_keys(self) -> None:
        entry = _make_entry()
        d = entry.to_dict()
        assert "source_id" in d
        assert "source_type" in d
        assert "status" in d
        assert "is_fetchable" in d

    def test_to_dict_status_is_string(self) -> None:
        entry = _make_entry()
        d = entry.to_dict()
        assert isinstance(d["status"], str)
        assert isinstance(d["source_type"], str)

    def test_default_credibility(self) -> None:
        entry = _make_entry()
        assert entry.credibility_score == 0.5

    def test_default_auth_mode(self) -> None:
        entry = _make_entry()
        assert entry.auth_mode == AuthMode.NONE


class TestSourceRegistry:
    def test_register_and_get(self) -> None:
        registry = SourceRegistry()
        entry = _make_entry("rss_test")
        registry.register(entry)
        assert registry.get("rss_test") is entry

    def test_get_missing_returns_none(self) -> None:
        registry = SourceRegistry()
        assert registry.get("nonexistent") is None

    def test_register_many(self) -> None:
        registry = SourceRegistry()
        entries = [_make_entry(f"source_{i}") for i in range(5)]
        registry.register_many(entries)
        assert len(registry) == 5

    def test_all_returns_all(self) -> None:
        registry = SourceRegistry()
        entries = [_make_entry(f"s{i}") for i in range(3)]
        registry.register_many(entries)
        assert len(registry.all()) == 3

    def test_fetchable_filters_inactive(self) -> None:
        registry = SourceRegistry()
        registry.register(_make_entry("active_1", status=SourceStatus.ACTIVE))
        registry.register(_make_entry("active_2", status=SourceStatus.ACTIVE))
        registry.register(_make_entry("disabled_1", status=SourceStatus.REQUIRES_API))
        registry.register(_make_entry("disabled_2", status=SourceStatus.DISABLED))
        fetchable = registry.fetchable()
        assert len(fetchable) == 2
        assert all(s.source_id.startswith("active_") for s in fetchable)

    def test_by_type(self) -> None:
        registry = SourceRegistry()
        registry.register(_make_entry("rss_1", source_type=SourceType.RSS_FEED))
        registry.register(_make_entry("rss_2", source_type=SourceType.RSS_FEED))
        registry.register(_make_entry("yt_1", source_type=SourceType.YOUTUBE_CHANNEL))
        rss = registry.by_type(SourceType.RSS_FEED)
        yt = registry.by_type(SourceType.YOUTUBE_CHANNEL)
        assert len(rss) == 2
        assert len(yt) == 1

    def test_by_status(self) -> None:
        registry = SourceRegistry()
        registry.register(_make_entry("a", status=SourceStatus.ACTIVE))
        registry.register(_make_entry("b", status=SourceStatus.REQUIRES_API))
        registry.register(_make_entry("c", status=SourceStatus.REQUIRES_API))
        assert len(registry.by_status(SourceStatus.ACTIVE)) == 1
        assert len(registry.by_status(SourceStatus.REQUIRES_API)) == 2

    def test_overwrite_on_duplicate_id(self) -> None:
        registry = SourceRegistry()
        entry1 = _make_entry("dup_source")
        entry2 = SourceEntry(
            source_id="dup_source",
            source_name="Updated Name",
            source_type=SourceType.RSS_FEED,
            status=SourceStatus.ACTIVE,
            url="https://new-url.example.com/feed",
        )
        registry.register(entry1)
        registry.register(entry2)
        assert len(registry) == 1
        assert registry.get("dup_source").source_name == "Updated Name"

    def test_summary(self) -> None:
        registry = SourceRegistry()
        registry.register(_make_entry("a", source_type=SourceType.RSS_FEED, status=SourceStatus.ACTIVE))
        registry.register(_make_entry("b", source_type=SourceType.RSS_FEED, status=SourceStatus.ACTIVE))
        registry.register(_make_entry("c", source_type=SourceType.YOUTUBE_CHANNEL, status=SourceStatus.REQUIRES_API))
        summary = registry.summary()
        assert summary["total"] == 3
        assert summary["fetchable"] == 2
        assert summary["by_type"]["rss_feed"] == 2
        assert summary["by_type"]["youtube_channel"] == 1

    def test_len(self) -> None:
        registry = SourceRegistry()
        assert len(registry) == 0
        registry.register(_make_entry("x"))
        assert len(registry) == 1


class TestBuildRegistry:
    def test_build_from_website_sources(self) -> None:
        website_sources = [
            {"domain": "example.com", "name": "Example", "type": "website",
             "language": "en", "category": "news", "status": "active"},
        ]
        registry = build_registry(website_sources=website_sources)
        assert len(registry) == 1
        entry = registry.get("example_com")
        assert entry is not None
        assert entry.source_name == "Example"

    def test_build_from_rss_feeds(self) -> None:
        rss_feeds = [
            {"source_id": "coindesk_rss", "title": "CoinDesk", "rss_url": "https://coindesk.com/feed", "status": "active"},
        ]
        registry = build_registry(rss_feeds=rss_feeds)
        assert len(registry) == 1
        entry = registry.get("coindesk_rss")
        assert entry is not None
        assert entry.source_type == SourceType.RSS_FEED

    def test_skip_invalid_entries(self) -> None:
        bad_sources = [{"no_domain": "value"}]  # Missing required "domain" key
        # Should not raise, just skip
        registry = build_registry(website_sources=bad_sources)
        assert len(registry) == 0

    def test_build_combined(self) -> None:
        website_sources = [
            {"domain": "site.com", "name": "Site", "type": "website",
             "language": "en", "category": "news", "status": "active"},
        ]
        rss_feeds = [
            {"source_id": "feed_1", "title": "Feed 1", "rss_url": "https://site.com/rss", "status": "active"},
        ]
        registry = build_registry(website_sources=website_sources, rss_feeds=rss_feeds)
        assert len(registry) == 2

    def test_build_empty(self) -> None:
        registry = build_registry()
        assert len(registry) == 0
