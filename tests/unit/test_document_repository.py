from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.domain.document import AnalysisResult, CanonicalDocument
from app.core.enums import AnalysisSource, DocumentStatus, MarketScope, SentimentLabel
from app.storage.db.session import Base
from app.storage.repositories.document_repo import DocumentRepository


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_save_sets_persisted_status(session_factory) -> None:
    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        saved = await repo.save(
            CanonicalDocument(
                url="https://example.com/article-1",
                title="Bitcoin jumps",
            )
        )

    assert saved.status == DocumentStatus.PERSISTED
    assert saved.is_duplicate is False
    assert saved.is_analyzed is False

    async with session_factory() as session:
        repo = DocumentRepository(session)
        stored = await repo.get_by_id(str(saved.id))

    assert stored is not None
    assert stored.status == DocumentStatus.PERSISTED
    assert stored.is_duplicate is False
    assert stored.is_analyzed is False


@pytest.mark.asyncio
async def test_mark_duplicate_sets_duplicate_status(session_factory) -> None:
    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        saved = await repo.save(
            CanonicalDocument(
                url="https://example.com/article-2",
                title="Ethereum jumps",
            )
        )
        await repo.mark_duplicate(str(saved.id))

    async with session_factory() as session:
        repo = DocumentRepository(session)
        stored = await repo.get_by_id(str(saved.id))

    assert stored is not None
    assert stored.status == DocumentStatus.DUPLICATE
    assert stored.is_duplicate is True
    assert stored.is_analyzed is False


@pytest.mark.asyncio
async def test_mark_analyzed_sets_analyzed_status(session_factory) -> None:
    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        saved = await repo.save(
            CanonicalDocument(
                url="https://example.com/article-3",
                title="Solana jumps",
            )
        )
        await repo.mark_analyzed(str(saved.id))

    async with session_factory() as session:
        repo = DocumentRepository(session)
        stored = await repo.get_by_id(str(saved.id))

    assert stored is not None
    assert stored.status == DocumentStatus.ANALYZED
    assert stored.is_duplicate is False
    assert stored.is_analyzed is True


@pytest.mark.asyncio
async def test_get_pending_documents_returns_only_persisted_docs(session_factory) -> None:
    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        pending = await repo.save(
            CanonicalDocument(
                url="https://example.com/article-pending",
                title="Pending article",
            )
        )
        duplicate = await repo.save(
            CanonicalDocument(
                url="https://example.com/article-duplicate",
                title="Duplicate article",
            )
        )
        failed = await repo.save(
            CanonicalDocument(
                url="https://example.com/article-failed",
                title="Failed article",
            )
        )
        analyzed = await repo.save(
            CanonicalDocument(
                url="https://example.com/article-analyzed",
                title="Analyzed article",
            )
        )
        await repo.mark_duplicate(str(duplicate.id))
        await repo.mark_failed(str(failed.id))
        await repo.mark_analyzed(str(analyzed.id))

    async with session_factory() as session:
        repo = DocumentRepository(session)
        docs = await repo.get_pending_documents(limit=10)

    assert [str(doc.id) for doc in docs] == [str(pending.id)]
    assert docs[0].status == DocumentStatus.PERSISTED
    assert docs[0].is_duplicate is False
    assert docs[0].is_analyzed is False


@pytest.mark.asyncio
async def test_update_analysis_sets_analyzed_status(session_factory) -> None:
    published_at = datetime.now(UTC)

    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        saved = await repo.save(
            CanonicalDocument(
                url="https://example.com/article-4",
                title="Macro update",
                published_at=published_at,
            )
        )
        analysis_result = AnalysisResult(
            document_id=str(saved.id),
            analysis_source=AnalysisSource.INTERNAL,
            sentiment_label=SentimentLabel.BULLISH,
            sentiment_score=0.7,
            relevance_score=0.8,
            impact_score=0.6,
            novelty_score=0.5,
            confidence_score=0.9,
            spam_probability=0.1,
            recommended_priority=7,
            market_scope=MarketScope.UNKNOWN,
            explanation_short="Test",
            explanation_long="Test long",
            tags=["macro"],
            affected_sectors=["defi", "layer1"],
        )
        await repo.update_analysis(
            str(saved.id),
            analysis_result,
            provider_name="shadow",
            metadata_updates={"ensemble_chain": ["openai", "shadow"]},
        )

    async with session_factory() as session:
        repo = DocumentRepository(session)
        stored = await repo.get_by_id(str(saved.id))

    assert stored is not None
    assert stored.status == DocumentStatus.ANALYZED
    assert stored.is_duplicate is False
    assert stored.is_analyzed is True
    assert stored.provider == "shadow"
    assert stored.analysis_source == AnalysisSource.INTERNAL
    assert stored.effective_analysis_source == AnalysisSource.INTERNAL
    assert stored.metadata["ensemble_chain"] == ["openai", "shadow"]
    assert stored.sentiment_label == SentimentLabel.BULLISH
    assert stored.priority_score == analysis_result.recommended_priority
    assert stored.categories == ["defi", "layer1"]


@pytest.mark.asyncio
async def test_source_activity_aggregates_per_source(session_factory) -> None:
    from datetime import timedelta

    from app.storage.models.document import CanonicalDocumentModel

    now = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)
    rows = [
        ("rss", now - timedelta(hours=1)),
        ("rss", now - timedelta(hours=2)),
        ("rss", now - timedelta(hours=50)),  # outside the 24h window
        ("okx", now - timedelta(hours=3)),
        (None, now - timedelta(hours=4)),  # null source → "unknown"
    ]
    async with session_factory.begin() as session:
        for i, (src, fetched) in enumerate(rows):
            session.add(
                CanonicalDocumentModel(
                    id=f"doc-{i}",
                    url=f"https://example.com/{i}",
                    title=f"t{i}",
                    document_type="news",
                    source_name=src,
                    fetched_at=fetched,
                )
            )

    async with session_factory() as session:
        repo = DocumentRepository(session)
        result = await repo.source_activity(window_hours=24, now=now)

    by = {r.source_name: r for r in result}
    assert by["rss"].total == 3 and by["rss"].window_count == 2  # 50h-old excluded
    assert by["okx"].total == 1 and by["okx"].window_count == 1
    assert by["unknown"].total == 1  # null source coalesced
    assert by["rss"].last_fetched_at is not None
    assert by["rss"].silent is False  # within the 7d silence threshold
    # newest source first: rss (last fetch 1h ago) before okx (3h ago)
    assert result[0].source_name == "rss"


@pytest.mark.asyncio
async def test_source_activity_silent_flag(session_factory) -> None:
    from datetime import timedelta

    from app.storage.models.document import CanonicalDocumentModel

    now = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)
    rows = [
        ("fresh", now - timedelta(hours=2)),  # recent → not silent
        ("dead", now - timedelta(hours=200)),  # > 168h → silent
    ]
    async with session_factory.begin() as session:
        for i, (src, fetched) in enumerate(rows):
            session.add(
                CanonicalDocumentModel(
                    id=f"sil-{i}",
                    url=f"https://example.com/sil/{i}",
                    title=f"t{i}",
                    document_type="news",
                    source_name=src,
                    fetched_at=fetched,
                )
            )

    async with session_factory() as session:
        repo = DocumentRepository(session)
        result = await repo.source_activity(silent_after_hours=168, now=now)

    by = {r.source_name: r for r in result}
    assert by["fresh"].silent is False
    assert by["dead"].silent is True  # nothing in 7 days → went quiet


@pytest.mark.asyncio
async def test_source_activity_empty_store(session_factory) -> None:
    async with session_factory() as session:
        repo = DocumentRepository(session)
        assert await repo.source_activity() == []


@pytest.mark.asyncio
async def test_list_directional_news_events_filters_orders_and_windows(session_factory) -> None:
    from app.research.news_outcomes import load_news_events
    from app.storage.models.document import CanonicalDocumentModel

    def _doc(**kw):
        base = {"document_type": "news", "status": "analyzed", "market_scope": "crypto"}
        base.update(kw)
        return CanonicalDocumentModel(**base)

    async with session_factory.begin() as session:
        session.add_all(
            [
                _doc(
                    id="d1",
                    url="u1",
                    title="BTC up",
                    source_name="cointelegraph",
                    sentiment_label="bullish",
                    tickers=["BTC/USDT"],
                    published_at=datetime(2026, 6, 15, tzinfo=UTC),
                    directional_confidence=0.8,
                ),
                _doc(
                    id="d2",
                    url="u2",
                    title="ETH down",
                    source_name="decrypt",
                    sentiment_label="bearish",
                    tickers=["ETH/USDT"],
                    published_at=datetime(2026, 6, 20, tzinfo=UTC),
                    directional_confidence=0.5,
                ),
                _doc(  # excluded: neutral
                    id="d3",
                    url="u3",
                    title="meh",
                    sentiment_label="neutral",
                    tickers=["BTC/USDT"],
                    published_at=datetime(2026, 6, 16, tzinfo=UTC),
                ),
                _doc(  # passes coarse SQL filter, dropped by load_news_events (empty tickers)
                    id="d4",
                    url="u4",
                    title="vague bull",
                    source_name="empty",
                    sentiment_label="bullish",
                    tickers=[],
                    published_at=datetime(2026, 6, 17, tzinfo=UTC),
                ),
            ]
        )

    async with session_factory() as session:
        repo = DocumentRepository(session)
        allrows = await repo.list_directional_news_events(since=None)
        strict = await repo.list_directional_news_events(min_confidence=0.7)
        windowed = await repo.list_directional_news_events(since=datetime(2026, 6, 18, tzinfo=UTC))

    # coarse SQL filter: drops neutral (sentiment), keeps directional; empty-ticker
    # doc slips through here and is dropped by the authority (load_news_events).
    assert all(r["sentiment_label"] != "neutral" for r in allrows)
    assert "cointelegraph" in {r["source_name"] for r in allrows}
    # min_confidence keeps only the 0.8 doc (NULL confidence excluded)
    assert [r["source_name"] for r in strict] == ["cointelegraph"]
    # since-window drops docs before the cutoff
    assert [r["source_name"] for r in windowed] == ["decrypt"]
    # the pure event loader is the authority: only real directional+ticker survive
    events = load_news_events(allrows)
    assert [(e.symbol, e.side) for e in events] == [
        ("BTC/USDT", "long"),
        ("ETH/USDT", "short"),
    ]
