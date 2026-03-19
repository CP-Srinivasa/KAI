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
            provider_name="companion",
            metadata_updates={"ensemble_chain": ["openai", "companion"]},
        )

    async with session_factory() as session:
        repo = DocumentRepository(session)
        stored = await repo.get_by_id(str(saved.id))

    assert stored is not None
    assert stored.status == DocumentStatus.ANALYZED
    assert stored.is_duplicate is False
    assert stored.is_analyzed is True
    assert stored.provider == "companion"
    assert stored.analysis_source == AnalysisSource.INTERNAL
    assert stored.effective_analysis_source == AnalysisSource.INTERNAL
    assert stored.metadata["ensemble_chain"] == ["openai", "companion"]
    assert stored.sentiment_label == SentimentLabel.BULLISH
    assert stored.priority_score == analysis_result.recommended_priority
    assert stored.categories == ["defi", "layer1"]
