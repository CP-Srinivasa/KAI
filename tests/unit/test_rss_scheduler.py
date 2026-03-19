from datetime import UTC, datetime

import pytest

from app.core.domain.document import CanonicalDocument
from app.core.enums import AuthMode, SourceStatus, SourceType
from app.core.settings import AppSettings
from app.ingestion.base.interfaces import FetchResult
from app.ingestion.classifier import ClassificationResult
from app.ingestion.resolvers.rss import RSSResolveResult
from app.ingestion.rss.service import RSSCollectedFeed
from app.ingestion.schedulers import rss_scheduler
from app.storage.document_ingest import IngestPersistStats
from app.storage.schemas.source import SourceRead


def _source() -> SourceRead:
    now = datetime.now(UTC)
    return SourceRead(
        source_id="src-1",
        source_type=SourceType.RSS_FEED,
        provider="rss",
        status=SourceStatus.ACTIVE,
        auth_mode=AuthMode.NONE,
        original_url="https://example.com/feed",
        normalized_url="https://example.com/feed.xml",
        notes=None,
        created_at=now,
        updated_at=now,
    )


def _collected_feed(*docs: CanonicalDocument) -> RSSCollectedFeed:
    return RSSCollectedFeed(
        classification=ClassificationResult(SourceType.RSS_FEED, SourceStatus.ACTIVE),
        resolved_feed=RSSResolveResult(
            url="https://example.com/feed",
            is_valid=True,
            resolved_url="https://example.com/feed.xml",
            feed_title="Example Feed",
            entry_count=len(docs),
            error=None,
        ),
        fetch_result=FetchResult(
            source_id="src-1",
            documents=list(docs),
            fetched_at=datetime.now(UTC),
            success=True,
        ),
    )


@pytest.mark.asyncio
async def test_scheduler_poll_one_uses_async_persist_callback_and_notifies(
    monkeypatch,
) -> None:
    doc = CanonicalDocument(url="https://example.com/article-1", title="Bitcoin jumps")
    collected = _collected_feed(doc)
    persisted_results: list[FetchResult] = []
    callback_results: list[FetchResult] = []

    async def fake_collect_rss_feed(**kwargs) -> RSSCollectedFeed:
        assert kwargs["source_id"] == "src-1"
        assert kwargs["url"] == "https://example.com/feed.xml"
        return collected

    async def fake_persist_result(result: FetchResult) -> IngestPersistStats:
        persisted_results.append(result)
        return IngestPersistStats(
            fetched_count=1,
            candidate_count=1,
            batch_duplicates=0,
            existing_duplicates=0,
            saved_count=1,
            failed_count=0,
            preview_documents=[doc],
        )

    async def fake_on_result(result: FetchResult) -> None:
        callback_results.append(result)

    monkeypatch.setattr(rss_scheduler, "collect_rss_feed", fake_collect_rss_feed)
    monkeypatch.setattr(rss_scheduler, "get_settings", lambda: AppSettings())

    scheduler = rss_scheduler.RSSScheduler(
        "session-factory",
        persist_result=fake_persist_result,
        on_result=fake_on_result,
    )

    await scheduler._poll_one(_source())

    assert persisted_results == [collected.fetch_result]
    assert callback_results == [collected.fetch_result]


@pytest.mark.asyncio
async def test_scheduler_poll_one_uses_default_persist_service(monkeypatch) -> None:
    doc = CanonicalDocument(url="https://example.com/article-1", title="Bitcoin jumps")
    collected = _collected_feed(doc)
    persisted_results: list[FetchResult] = []

    async def fake_collect_rss_feed(**kwargs) -> RSSCollectedFeed:
        return collected

    async def fake_persist_fetch_result(session_factory, result: FetchResult) -> IngestPersistStats:
        assert session_factory == "session-factory"
        persisted_results.append(result)
        return IngestPersistStats(
            fetched_count=1,
            candidate_count=1,
            batch_duplicates=0,
            existing_duplicates=0,
            saved_count=1,
            failed_count=0,
            preview_documents=[doc],
        )

    monkeypatch.setattr(rss_scheduler, "collect_rss_feed", fake_collect_rss_feed)
    monkeypatch.setattr(rss_scheduler, "persist_fetch_result", fake_persist_fetch_result)
    monkeypatch.setattr(rss_scheduler, "get_settings", lambda: AppSettings())

    scheduler = rss_scheduler.RSSScheduler("session-factory")

    await scheduler._poll_one(_source())

    assert persisted_results == [collected.fetch_result]


@pytest.mark.asyncio
async def test_scheduler_poll_one_accepts_sync_persist_callback(monkeypatch) -> None:
    doc = CanonicalDocument(url="https://example.com/article-1", title="Bitcoin jumps")
    collected = _collected_feed(doc)
    persisted_results: list[FetchResult] = []

    async def fake_collect_rss_feed(**kwargs) -> RSSCollectedFeed:
        return collected

    def fake_persist_result(result: FetchResult) -> IngestPersistStats:
        persisted_results.append(result)
        return IngestPersistStats(
            fetched_count=1,
            candidate_count=1,
            batch_duplicates=0,
            existing_duplicates=0,
            saved_count=1,
            failed_count=0,
            preview_documents=[doc],
        )

    monkeypatch.setattr(rss_scheduler, "collect_rss_feed", fake_collect_rss_feed)
    monkeypatch.setattr(rss_scheduler, "get_settings", lambda: AppSettings())

    scheduler = rss_scheduler.RSSScheduler(
        "session-factory",
        persist_result=fake_persist_result,
    )

    await scheduler._poll_one(_source())

    assert persisted_results == [collected.fetch_result]


@pytest.mark.asyncio
async def test_scheduler_poll_one_survives_persist_failure(monkeypatch) -> None:
    doc = CanonicalDocument(url="https://example.com/article-1", title="Bitcoin jumps")
    collected = _collected_feed(doc)
    callback_results: list[FetchResult] = []

    async def fake_collect_rss_feed(**kwargs) -> RSSCollectedFeed:
        return collected

    async def fake_persist_result(result: FetchResult) -> IngestPersistStats:
        raise RuntimeError("database unavailable")

    async def fake_on_result(result: FetchResult) -> None:
        callback_results.append(result)

    monkeypatch.setattr(rss_scheduler, "collect_rss_feed", fake_collect_rss_feed)
    monkeypatch.setattr(rss_scheduler, "get_settings", lambda: AppSettings())

    scheduler = rss_scheduler.RSSScheduler(
        "session-factory",
        persist_result=fake_persist_result,
        on_result=fake_on_result,
    )

    await scheduler._poll_one(_source())

    assert callback_results == [collected.fetch_result]


@pytest.mark.asyncio
async def test_scheduler_poll_one_survives_callback_failure(monkeypatch) -> None:
    doc = CanonicalDocument(url="https://example.com/article-1", title="Bitcoin jumps")
    collected = _collected_feed(doc)
    persisted_results: list[FetchResult] = []

    async def fake_collect_rss_feed(**kwargs) -> RSSCollectedFeed:
        return collected

    async def fake_persist_result(result: FetchResult) -> IngestPersistStats:
        persisted_results.append(result)
        return IngestPersistStats(
            fetched_count=1,
            candidate_count=1,
            batch_duplicates=0,
            existing_duplicates=0,
            saved_count=1,
            failed_count=0,
            preview_documents=[doc],
        )

    async def fake_on_result(result: FetchResult) -> None:
        raise RuntimeError("callback failed")

    monkeypatch.setattr(rss_scheduler, "collect_rss_feed", fake_collect_rss_feed)
    monkeypatch.setattr(rss_scheduler, "get_settings", lambda: AppSettings())

    scheduler = rss_scheduler.RSSScheduler(
        "session-factory",
        persist_result=fake_persist_result,
        on_result=fake_on_result,
    )

    await scheduler._poll_one(_source())

    assert persisted_results == [collected.fetch_result]
