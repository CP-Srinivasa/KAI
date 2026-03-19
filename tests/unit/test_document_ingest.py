"""Tests for fetched-document persistence helpers."""

from __future__ import annotations

from app.core.domain.document import CanonicalDocument
from app.core.enums import DocumentStatus
from app.ingestion.base.interfaces import FetchResult
from app.storage import document_ingest


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


def test_prepare_ingested_document_normalizes_identity_fields() -> None:
    prepared = document_ingest.prepare_ingested_document(
        CanonicalDocument(
            url="https://www.example.com/article?utm_source=rss",
            title="Bitcoin hits $100K!",
            raw_text="alpha",
            is_duplicate=True,
            is_analyzed=True,
        )
    )

    assert prepared.url == "https://example.com/article"
    assert prepared.content_hash is not None
    assert prepared.status == DocumentStatus.PENDING
    assert prepared.is_duplicate is False
    assert prepared.is_analyzed is False
    assert prepared.metadata["original_url"] == "https://www.example.com/article?utm_source=rss"
    assert prepared.metadata["normalized_url"] == "https://example.com/article"
    assert prepared.metadata["normalized_title"] == "bitcoin hits 100k"


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
    assert len(saved_docs) == 1
