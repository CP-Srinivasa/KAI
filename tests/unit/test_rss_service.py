"""Tests for the shared RSS collection workflow."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.domain.document import CanonicalDocument
from app.core.enums import SourceStatus, SourceType
from app.ingestion.base.interfaces import FetchResult
from app.ingestion.classifier import ClassificationResult
from app.ingestion.resolvers.rss import RSSResolveResult
from app.ingestion.rss import service as rss_service


def _docs() -> list[CanonicalDocument]:
    return [CanonicalDocument(url="https://example.com/article-1", title="Bitcoin rises")]


@pytest.mark.asyncio
async def test_collect_rss_feed_success(monkeypatch, tmp_path) -> None:
    docs = _docs()

    class FakeClassifier:
        def classify(self, url: str) -> ClassificationResult:
            return ClassificationResult(SourceType.RSS_FEED, SourceStatus.ACTIVE, "Looks valid")

    async def fake_resolve(url: str, timeout: int = 10) -> RSSResolveResult:
        return RSSResolveResult(
            url=url,
            is_valid=True,
            resolved_url="https://example.com/feed.xml",
            feed_title="Test Feed",
            entry_count=len(docs),
        )

    async def fake_fetch(self) -> FetchResult:
        return FetchResult(
            source_id=self.source_id,
            documents=docs,
            fetched_at=datetime.now(UTC),
            success=True,
        )

    monkeypatch.setattr(
        rss_service.SourceClassifier,
        "from_monitor_dir",
        lambda monitor_dir: FakeClassifier(),
    )
    monkeypatch.setattr(rss_service, "resolve_rss_feed", fake_resolve)
    monkeypatch.setattr(rss_service.RSSFeedAdapter, "fetch", fake_fetch)

    collected = await rss_service.collect_rss_feed(
        url="https://example.com/feed",
        source_id="src-1",
        source_name="Feed",
        monitor_dir=tmp_path,
    )

    assert collected.fetch_result.success is True
    assert collected.fetch_result.documents == docs
    assert collected.fetch_result.metadata["classified_source_type"] == "rss_feed"
    assert collected.fetch_result.metadata["resolved_url"] == "https://example.com/feed.xml"
    assert collected.resolved_feed.feed_title == "Test Feed"


@pytest.mark.asyncio
async def test_collect_rss_feed_invalid_feed_returns_failure(monkeypatch, tmp_path) -> None:
    class FakeClassifier:
        def classify(self, url: str) -> ClassificationResult:
            return ClassificationResult(SourceType.WEBSITE, SourceStatus.ACTIVE)

    async def fake_resolve(url: str, timeout: int = 10) -> RSSResolveResult:
        return RSSResolveResult(
            url=url,
            is_valid=False,
            resolved_url=None,
            feed_title=None,
            entry_count=0,
            error="Response is not a valid RSS or Atom feed",
        )

    monkeypatch.setattr(
        rss_service.SourceClassifier,
        "from_monitor_dir",
        lambda monitor_dir: FakeClassifier(),
    )
    monkeypatch.setattr(rss_service, "resolve_rss_feed", fake_resolve)

    collected = await rss_service.collect_rss_feed(
        url="https://example.com",
        source_id="src-1",
        source_name="Feed",
        monitor_dir=tmp_path,
    )

    assert collected.fetch_result.success is False
    assert collected.fetch_result.documents == []
    assert "website" in (collected.fetch_result.error or "")
    assert collected.fetch_result.metadata["classified_source_type"] == "website"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rejected_type",
    [SourceType.YOUTUBE_CHANNEL, SourceType.PODCAST_PAGE, SourceType.WEBSITE],
)
async def test_collect_rss_feed_rejects_non_feed_source_types(
    monkeypatch, tmp_path, rejected_type: SourceType
) -> None:
    """YouTube, podcast landing pages etc. must be rejected before any HTTP call."""

    def make_classifier(st: SourceType):
        class _Cls:
            def classify(self, url: str) -> ClassificationResult:
                return ClassificationResult(st, SourceStatus.ACTIVE)

        return _Cls()

    resolve_called = False

    async def fake_resolve(url: str, timeout: int = 10) -> RSSResolveResult:
        nonlocal resolve_called
        resolve_called = True
        raise AssertionError("resolve_rss_feed must not be called for non-feed types")

    monkeypatch.setattr(
        rss_service.SourceClassifier,
        "from_monitor_dir",
        lambda monitor_dir: make_classifier(rejected_type),
    )
    monkeypatch.setattr(rss_service, "resolve_rss_feed", fake_resolve)

    collected = await rss_service.collect_rss_feed(
        url="https://example.com",
        source_id="src-1",
        source_name="Source",
        monitor_dir=tmp_path,
    )

    assert collected.fetch_result.success is False
    assert resolve_called is False
    assert rejected_type.value in (collected.fetch_result.error or "")


@pytest.mark.asyncio
async def test_collect_rss_feed_wraps_fetch_failures(monkeypatch, tmp_path) -> None:
    class FakeClassifier:
        def classify(self, url: str) -> ClassificationResult:
            return ClassificationResult(SourceType.RSS_FEED, SourceStatus.ACTIVE)

    async def fake_resolve(url: str, timeout: int = 10) -> RSSResolveResult:
        return RSSResolveResult(
            url=url,
            is_valid=True,
            resolved_url="https://example.com/feed.xml",
            feed_title="Test Feed",
            entry_count=0,
        )

    async def fake_fetch(self) -> FetchResult:
        return FetchResult(
            source_id=self.source_id,
            documents=[],
            fetched_at=datetime.now(UTC),
            success=False,
            error="timeout",
        )

    monkeypatch.setattr(
        rss_service.SourceClassifier,
        "from_monitor_dir",
        lambda monitor_dir: FakeClassifier(),
    )
    monkeypatch.setattr(rss_service, "resolve_rss_feed", fake_resolve)
    monkeypatch.setattr(rss_service.RSSFeedAdapter, "fetch", fake_fetch)

    collected = await rss_service.collect_rss_feed(
        url="https://example.com/feed",
        source_id="src-1",
        source_name="Feed",
        monitor_dir=tmp_path,
    )

    assert collected.fetch_result.success is False
    assert "RSS fetch failed for https://example.com/feed.xml" in (
        collected.fetch_result.error or ""
    )
