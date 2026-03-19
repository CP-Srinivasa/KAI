from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.domain.document import CanonicalDocument
from app.core.enums import AuthMode, DocumentStatus, SourceStatus, SourceType
from app.core.settings import AppSettings
from app.ingestion.base.interfaces import FetchResult
from app.ingestion.classifier import ClassificationResult
from app.ingestion.resolvers.rss import RSSResolveResult
from app.ingestion.rss.service import RSSCollectedFeed
from app.ingestion.schedulers import rss_scheduler
from app.storage.db.session import Base
from app.storage.document_ingest import IngestPersistStats
from app.storage.repositories.document_repo import DocumentRepository
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


@pytest.fixture
async def db_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


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


@pytest.mark.asyncio
async def test_scheduler_poll_one_persists_documents_via_storage_service(
    monkeypatch,
    db_session_factory,
) -> None:
    doc = CanonicalDocument(
        url="https://www.example.com/article-1?utm_source=rss",
        title=" Bitcoin jumps ",
        raw_text="<p>body</p>",
        source_id="src-1",
        source_name="rss",
        source_type=SourceType.RSS_FEED,
    )
    collected = _collected_feed(doc)

    async def fake_collect_rss_feed(**kwargs) -> RSSCollectedFeed:
        return collected

    monkeypatch.setattr(rss_scheduler, "collect_rss_feed", fake_collect_rss_feed)
    monkeypatch.setattr(rss_scheduler, "get_settings", lambda: AppSettings())

    scheduler = rss_scheduler.RSSScheduler(db_session_factory)

    await scheduler._poll_one(_source())

    async with db_session_factory() as session:
        repo = DocumentRepository(session)
        stored = await repo.get_by_url("https://example.com/article-1")
        pending_docs = await repo.get_pending_documents(limit=10)

    assert stored is not None
    assert stored.status == DocumentStatus.PERSISTED
    assert stored.is_duplicate is False
    assert stored.is_analyzed is False
    assert stored.metadata["normalized_url"] == "https://example.com/article-1"
    assert [doc.url for doc in pending_docs] == ["https://example.com/article-1"]


@pytest.mark.asyncio
async def test_scheduler_poll_one_isolates_invalid_documents_in_storage_path(
    monkeypatch,
    db_session_factory,
) -> None:
    bad_doc = CanonicalDocument(
        url="javascript:alert(1)",
        title="Bad link",
        raw_text="x",
        source_id="src-1",
        source_name="rss",
        source_type=SourceType.RSS_FEED,
    )
    good_doc = CanonicalDocument(
        url="https://example.com/article-2?utm_source=rss",
        title="Valid story",
        raw_text="<p>body</p>",
        source_id="src-1",
        source_name="rss",
        source_type=SourceType.RSS_FEED,
    )
    collected = _collected_feed(bad_doc, good_doc)

    async def fake_collect_rss_feed(**kwargs) -> RSSCollectedFeed:
        return collected

    monkeypatch.setattr(rss_scheduler, "collect_rss_feed", fake_collect_rss_feed)
    monkeypatch.setattr(rss_scheduler, "get_settings", lambda: AppSettings())

    scheduler = rss_scheduler.RSSScheduler(db_session_factory)

    await scheduler._poll_one(_source())

    async with db_session_factory() as session:
        repo = DocumentRepository(session)
        all_docs = await repo.list(limit=10)
        pending_docs = await repo.get_pending_documents(limit=10)

    assert len(all_docs) == 1
    assert all_docs[0].status == DocumentStatus.PERSISTED
    assert all_docs[0].url == "https://example.com/article-2"
    assert [doc.url for doc in pending_docs] == ["https://example.com/article-2"]
