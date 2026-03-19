"""Tests for fetched-document persistence helpers."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.domain.document import CanonicalDocument
from app.core.enums import DocumentStatus
from app.ingestion.base.interfaces import FetchResult
from app.storage import document_ingest
from app.storage.db.session import Base
from app.storage.repositories.document_repo import DocumentRepository


def _result(*docs: CanonicalDocument, success: bool = True) -> FetchResult:
    from datetime import UTC, datetime

    return FetchResult(
        source_id="src-1",
        documents=list(docs),
        fetched_at=datetime.now(UTC),
        success=success,
    )


async def _fake_context_result():
    return object()


def _session_factory() -> object:
    class FakeSessionContext:
        async def __aenter__(self):
            return await _fake_context_result()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeSessionFactory:
        def begin(self) -> FakeSessionContext:
            return FakeSessionContext()

    return FakeSessionFactory()


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


def test_prepare_ingested_document_normalizes_identity_fields() -> None:
    prepared = document_ingest.prepare_ingested_document(
        CanonicalDocument(
            url="https://www.example.com/article?utm_source=rss",
            title=" Bitcoin hits $100K! ",
            raw_text="<p>alpha</p>",
            is_duplicate=True,
            is_analyzed=True,
        )
    )

    assert prepared.url == "https://example.com/article"
    assert prepared.title == "Bitcoin hits $100K!"
    assert prepared.raw_text == "alpha"
    assert prepared.content_hash is not None
    assert prepared.status == DocumentStatus.PENDING
    assert prepared.is_duplicate is False
    assert prepared.is_analyzed is False
    assert prepared.metadata["original_url"] == "https://www.example.com/article?utm_source=rss"
    assert prepared.metadata["normalized_url"] == "https://example.com/article"
    assert prepared.metadata["normalized_title"] == "bitcoin hits 100k"


def test_prepare_ingested_document_enforces_field_limits() -> None:
    prepared = document_ingest.prepare_ingested_document(
        CanonicalDocument(
            url="https://example.com/article",
            external_id="x" * 600,
            title="T" * 1200,
            raw_text="a" * 60_000,
        )
    )

    assert prepared.external_id is not None
    assert len(prepared.external_id) == 512
    assert len(prepared.title) == 1000
    assert prepared.raw_text is not None
    assert len(prepared.raw_text) == 50_000


async def test_persist_fetch_result_dry_run_normalizes_and_deduplicates() -> None:
    docs = [
        CanonicalDocument(
            url="https://www.example.com/article?utm_source=x",
            title="Bitcoin hits $100K!",
            raw_text="alpha",
        ),
        CanonicalDocument(
            url="https://example.com/article",
            title="Bitcoin Hits 100K",
            raw_text="alpha",
        ),
    ]

    stats = await document_ingest.persist_fetch_result(None, _result(*docs), dry_run=True)

    assert stats.fetched_count == 2
    assert stats.candidate_count == 1
    assert stats.batch_duplicates == 1
    assert len(stats.duplicate_documents) == 1
    assert stats.duplicate_documents[0].status == DocumentStatus.DUPLICATE
    assert stats.duplicate_documents[0].is_duplicate is True
    assert stats.preview_documents[0].url == "https://example.com/article"
    assert stats.preview_documents[0].metadata["normalized_title"] == "bitcoin hits 100k"


async def test_persist_fetch_result_skips_existing_duplicates_and_saves_unique(monkeypatch) -> None:
    saved_docs: list[CanonicalDocument] = []
    existing_doc = CanonicalDocument(
        url="https://archive.example.com/old",
        title="Bitcoin Hits 100K",
        raw_text="old story",
    )

    class FakeDocumentRepository:
        def __init__(self, session) -> None:
            self._session = session

        async def list(self, **kwargs) -> list[CanonicalDocument]:
            return [existing_doc]

        async def get_by_url(self, url: str):
            return None

        async def get_by_hash(self, content_hash: str):
            return None

        async def save_document(self, doc: CanonicalDocument) -> str:
            persisted = doc.model_copy(update={"status": DocumentStatus.PERSISTED})
            saved_docs.append(persisted)
            return str(doc.id)

        async def save(self, doc: CanonicalDocument) -> CanonicalDocument:
            persisted = doc.model_copy(update={"status": DocumentStatus.PERSISTED})
            saved_docs.append(persisted)
            return persisted

    monkeypatch.setattr(document_ingest, "DocumentRepository", FakeDocumentRepository)

    duplicate_title = CanonicalDocument(
        url="https://new.example.com/story",
        title="Bitcoin hits $100K!",
        raw_text="different body",
    )
    unique_doc = CanonicalDocument(
        url="https://feeds.example.com/story?utm_source=rss",
        title="Ethereum upgrade ships",
        raw_text="fresh",
    )

    stats = await document_ingest.persist_fetch_result(
        _session_factory(),
        _result(duplicate_title, unique_doc),
    )

    assert stats.candidate_count == 2
    assert stats.existing_duplicates == 1
    assert stats.saved_count == 1
    assert len(saved_docs) == 1
    assert saved_docs[0].url == "https://feeds.example.com/story"
    assert stats.preview_documents[0].status == DocumentStatus.PERSISTED
    assert len(stats.duplicate_documents) == 1
    assert stats.duplicate_documents[0].status == DocumentStatus.DUPLICATE
    assert saved_docs[0].is_duplicate is False
    assert saved_docs[0].is_analyzed is False
    assert saved_docs[0].metadata["normalized_title"] == "ethereum upgrade ships"


async def test_persist_fetch_result_continues_after_save_error(monkeypatch) -> None:
    saved_docs: list[CanonicalDocument] = []

    class FakeDocumentRepository:
        def __init__(self, session) -> None:
            self._session = session
            self._save_calls = 0

        async def list(self, **kwargs) -> list[CanonicalDocument]:
            return []

        async def get_by_url(self, url: str):
            return None

        async def get_by_hash(self, content_hash: str):
            return None

        async def save_document(self, doc: CanonicalDocument) -> str:
            self._save_calls += 1
            if self._save_calls == 1:
                raise RuntimeError("db unavailable")
            persisted = doc.model_copy(update={"status": DocumentStatus.PERSISTED})
            saved_docs.append(persisted)
            return str(doc.id)

    monkeypatch.setattr(document_ingest, "DocumentRepository", FakeDocumentRepository)

    stats = await document_ingest.persist_fetch_result(
        _session_factory(),
        _result(
            CanonicalDocument(url="https://example.com/1", title="One", raw_text="a"),
            CanonicalDocument(url="https://example.com/2", title="Two", raw_text="b"),
        ),
    )

    assert stats.saved_count == 1
    assert stats.failed_count == 1
    assert len(stats.errors) == 1
    assert "RuntimeError: db unavailable" in stats.errors
    assert len(stats.failed_documents) == 1
    assert stats.failed_documents[0].status == DocumentStatus.FAILED
    assert len(saved_docs) == 1


async def test_persist_fetch_result_rejects_invalid_document_urls(monkeypatch) -> None:
    saved_docs: list[CanonicalDocument] = []

    class FakeDocumentRepository:
        def __init__(self, session) -> None:
            self._session = session

        async def list(self, **kwargs) -> list[CanonicalDocument]:
            return []

        async def get_by_url(self, url: str):
            return None

        async def get_by_hash(self, content_hash: str):
            return None

        async def save_document(self, doc: CanonicalDocument) -> str:
            saved_docs.append(doc)
            return str(doc.id)

    monkeypatch.setattr(document_ingest, "DocumentRepository", FakeDocumentRepository)

    stats = await document_ingest.persist_fetch_result(
        _session_factory(),
        _result(
            CanonicalDocument(url="javascript:alert(1)", title="Bad link", raw_text="x"),
            CanonicalDocument(
                url="https://example.com/good?utm_source=rss",
                title=" Good link ",
                raw_text="<p>body</p>",
            ),
        ),
    )

    assert stats.saved_count == 1
    assert stats.failed_count == 1
    assert len(saved_docs) == 1
    assert saved_docs[0].url == "https://example.com/good"
    assert saved_docs[0].raw_text == "body"
    assert len(stats.failed_documents) == 1
    assert stats.failed_documents[0].status == DocumentStatus.FAILED
    assert "Unsupported or empty document URL" in stats.failed_documents[0].metadata["ingest_error"]


async def test_persist_fetch_result_treats_save_idempotency_as_duplicate(monkeypatch) -> None:
    existing_id = "existing-doc-id"

    class FakeDocumentRepository:
        def __init__(self, session) -> None:
            self._session = session

        async def list(self, **kwargs) -> list[CanonicalDocument]:
            return []

        async def get_by_url(self, url: str):
            return None

        async def get_by_hash(self, content_hash: str):
            return None

        async def save_document(self, doc: CanonicalDocument) -> str:
            return existing_id

    monkeypatch.setattr(document_ingest, "DocumentRepository", FakeDocumentRepository)

    stats = await document_ingest.persist_fetch_result(
        _session_factory(),
        _result(CanonicalDocument(url="https://example.com/idempotent", title="Same hash")),
    )

    assert stats.saved_count == 0
    assert stats.existing_duplicates == 1
    assert len(stats.duplicate_documents) == 1
    assert stats.duplicate_documents[0].status == DocumentStatus.DUPLICATE
    assert stats.duplicate_documents[0].metadata["duplicate_reasons"] == [
        "idempotent_hash_collision"
    ]


@pytest.mark.asyncio
async def test_persist_fetch_result_persists_documents_as_pending_queue_items(
    db_session_factory,
) -> None:
    stats = await document_ingest.persist_fetch_result(
        db_session_factory,
        _result(
            CanonicalDocument(
                url="https://www.example.com/article-1?utm_source=rss",
                title=" Bitcoin jumps ",
                raw_text="<p>body</p>",
            )
        ),
    )

    assert stats.saved_count == 1
    assert len(stats.preview_documents) == 1
    assert stats.preview_documents[0].status == DocumentStatus.PERSISTED
    assert stats.preview_documents[0].url == "https://example.com/article-1"
    assert stats.preview_documents[0].raw_text == "body"
    assert stats.preview_documents[0].is_duplicate is False
    assert stats.preview_documents[0].is_analyzed is False

    async with db_session_factory() as session:
        repo = DocumentRepository(session)
        stored = await repo.get_by_url("https://example.com/article-1")
        pending_docs = await repo.get_pending_documents(limit=10)

    assert stored is not None
    assert stored.status == DocumentStatus.PERSISTED
    assert stored.is_duplicate is False
    assert stored.is_analyzed is False
    assert stored.metadata["normalized_url"] == "https://example.com/article-1"
    assert stored.metadata["normalized_title"] == "bitcoin jumps"
    assert [doc.url for doc in pending_docs] == ["https://example.com/article-1"]
