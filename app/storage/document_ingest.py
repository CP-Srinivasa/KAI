"""Persistence helpers for fetched canonical documents.

The helpers here intentionally focus on storage preparation and save orchestration:
normalize identity fields, apply conservative dedup checks, and persist via the
DocumentRepository. No fetching, HTTP, or analysis logic lives here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.domain.document import CanonicalDocument
from app.core.enums import DocumentStatus
from app.enrichment.deduplication.deduplicator import Deduplicator
from app.ingestion.base.interfaces import FetchResult
from app.normalization.cleaner import clean_text, content_hash, normalize_title, normalize_url
from app.storage.repositories.document_repo import DocumentRepository

_INGEST_DEDUP_THRESHOLD = 0.85


@dataclass(frozen=True)
class IngestPersistStats:
    fetched_count: int
    candidate_count: int
    batch_duplicates: int
    existing_duplicates: int
    saved_count: int
    failed_count: int
    preview_documents: list[CanonicalDocument]
    errors: list[str] = field(default_factory=list)
    duplicate_documents: list[CanonicalDocument] = field(default_factory=list)
    failed_documents: list[CanonicalDocument] = field(default_factory=list)


def _with_status(
    doc: CanonicalDocument,
    *,
    status: DocumentStatus,
    duplicate_reasons: list[str] | None = None,
    ingest_error: str | None = None,
) -> CanonicalDocument:
    metadata = dict(doc.metadata)
    if duplicate_reasons:
        metadata["duplicate_reasons"] = duplicate_reasons
    if ingest_error:
        metadata["ingest_error"] = ingest_error
    return doc.model_copy(
        update={
            "status": status,
            "is_duplicate": status == DocumentStatus.DUPLICATE,
            "is_analyzed": False,
            "metadata": metadata,
        }
    )


def _is_supported_document_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def prepare_ingested_document(doc: CanonicalDocument) -> CanonicalDocument:
    """Normalize storage identity fields for a fetched document.

    - url is normalized before persistence so DB uniqueness uses canonical URLs
    - content_hash uses normalized url/title/body for stable dedup identity
    - normalized title/url are recorded in metadata for audit/debugging
    - raw_text is defensively cleaned again before storage
    """
    original_url = doc.url.strip()
    sanitized_title = doc.title.strip()
    sanitized_text = clean_text(doc.raw_text)
    normalized_url = normalize_url(original_url)
    normalized_title = normalize_title(sanitized_title)
    metadata = dict(doc.metadata)
    metadata.setdefault("original_url", original_url)
    metadata["normalized_url"] = normalized_url
    metadata["normalized_title"] = normalized_title

    return doc.model_copy(
        update={
            "url": normalized_url,
            "title": sanitized_title,
            "raw_text": sanitized_text,
            "content_hash": content_hash(normalized_url, sanitized_title, sanitized_text),
            "metadata": metadata,
            # status advances to PERSISTED after repo.save_document()
            "status": DocumentStatus.PENDING,
            "is_duplicate": False,
            "is_analyzed": False,
        }
    )


async def persist_fetch_result(
    session_factory: async_sessionmaker[AsyncSession] | None,
    result: FetchResult,
    *,
    dry_run: bool = False,
    existing_limit: int = 1000,
) -> IngestPersistStats:
    """Persist documents from a FetchResult with conservative dedup checks."""
    prepared_documents: list[CanonicalDocument] = []
    failed_documents: list[CanonicalDocument] = []
    errors: list[str] = []
    for doc in result.documents:
        prepared_doc = prepare_ingested_document(doc)
        if not _is_supported_document_url(prepared_doc.url):
            error = "Unsupported or empty document URL after normalization"
            failed_documents.append(
                _with_status(
                    prepared_doc,
                    status=DocumentStatus.FAILED,
                    ingest_error=error,
                )
            )
            errors.append(error)
            continue
        prepared_documents.append(prepared_doc)

    batch_dedup = Deduplicator(threshold=_INGEST_DEDUP_THRESHOLD)
    batch_scored = batch_dedup.filter_scored(prepared_documents)
    batch_candidates = [doc for doc, score in batch_scored if not score.is_duplicate]
    batch_duplicates = sum(1 for _, score in batch_scored if score.is_duplicate)
    duplicate_documents = [
        _with_status(
            doc,
            status=DocumentStatus.DUPLICATE,
            duplicate_reasons=score.reasons,
        )
        for doc, score in batch_scored
        if score.is_duplicate
    ]

    if dry_run or not result.success or not batch_candidates:
        return IngestPersistStats(
            fetched_count=len(result.documents),
            candidate_count=len(batch_candidates),
            batch_duplicates=batch_duplicates,
            existing_duplicates=0,
            saved_count=0,
            failed_count=len(failed_documents),
            preview_documents=batch_candidates,
            errors=errors,
            duplicate_documents=duplicate_documents,
            failed_documents=failed_documents,
        )

    if session_factory is None:
        raise ValueError("session_factory is required when dry_run is False")

    existing_duplicates = 0
    saved_count = 0
    failed_count = len(failed_documents)

    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        existing_docs = await repo.list(limit=existing_limit)
        existing_dedup = Deduplicator(threshold=_INGEST_DEDUP_THRESHOLD)
        for existing_doc in existing_docs:
            existing_dedup.register(prepare_ingested_document(existing_doc))

        save_candidates: list[CanonicalDocument] = []
        for doc in batch_candidates:
            if existing_dedup.is_duplicate(doc):
                existing_duplicates += 1
                duplicate_documents.append(
                    _with_status(
                        doc,
                        status=DocumentStatus.DUPLICATE,
                        duplicate_reasons=["existing_dedup"],
                    )
                )
                continue
            save_candidates.append(doc)
            existing_dedup.register(doc)

        saved_documents: list[CanonicalDocument] = []
        for doc in save_candidates:
            if await repo.get_by_url(doc.url):
                existing_duplicates += 1
                duplicate_documents.append(
                    _with_status(
                        doc,
                        status=DocumentStatus.DUPLICATE,
                        duplicate_reasons=["url_match"],
                    )
                )
                continue
            if doc.content_hash and await repo.get_by_hash(doc.content_hash):
                existing_duplicates += 1
                duplicate_documents.append(
                    _with_status(
                        doc,
                        status=DocumentStatus.DUPLICATE,
                        duplicate_reasons=["content_hash"],
                    )
                )
                continue
            try:
                saved_id = await repo.save_document(doc)
                saved_doc = doc.model_copy(
                    update={
                        "status": DocumentStatus.PERSISTED,
                        "is_duplicate": False,
                        "is_analyzed": False,
                    }
                )
            except Exception as err:
                failed_count += 1
                error = f"{type(err).__name__}: {err}"
                errors.append(error)
                failed_documents.append(
                    _with_status(
                        doc,
                        status=DocumentStatus.FAILED,
                        ingest_error=error,
                    )
                )
                continue
            if saved_id != str(doc.id):
                existing_duplicates += 1
                duplicate_documents.append(
                    _with_status(
                        doc,
                        status=DocumentStatus.DUPLICATE,
                        duplicate_reasons=["idempotent_hash_collision"],
                    )
                )
                continue
            saved_count += 1
            saved_documents.append(saved_doc)

    return IngestPersistStats(
        fetched_count=len(result.documents),
        candidate_count=len(batch_candidates),
        batch_duplicates=batch_duplicates,
        existing_duplicates=existing_duplicates,
        saved_count=saved_count,
        failed_count=failed_count,
        preview_documents=saved_documents,
        errors=errors,
        duplicate_documents=duplicate_documents,
        failed_documents=failed_documents,
    )
