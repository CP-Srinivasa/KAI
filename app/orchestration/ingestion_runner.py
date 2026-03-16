"""
Ingestion Runner
================
Orchestrates the full ingestion pipeline for a given source:

  Adapter.fetch()
    → DocumentDeduplicator.process_batch()
      → DocumentRepository.insert_many()
        → SourceRepository.mark_fetched() / mark_error()

The runner is the single entry point used by:
- CLI: `ingest rss --source <id>`
- Scheduler: RSSScheduler._fetch_one()
- API endpoints (on-demand fetch)

Each call is session-scoped: it creates an adapter, fetches, deduplicates,
persists, and closes cleanly.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import SourceType
from app.core.logging import get_logger
from app.enrichment.deduplication.deduplicator import DocumentDeduplicator
from app.ingestion.rss.adapter import RSSFeedAdapter
from app.ingestion.source_registry import SourceEntry, SourceRegistry, get_registry
from app.storage.models.db_models import CanonicalDocumentDB
from app.storage.repositories.document_repo import DocumentRepository
from app.storage.repositories.source_repo import SourceRepository

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

# Source types that the runner currently handles
_SUPPORTED_TYPES = {SourceType.RSS_FEED, SourceType.PODCAST_FEED}


def _build_adapter(entry: SourceEntry) -> RSSFeedAdapter:
    """
    Instantiate the correct adapter for a SourceEntry.
    Raises NotImplementedError for unsupported types.
    """
    if entry.source_type in (SourceType.RSS_FEED, SourceType.PODCAST_FEED):
        return RSSFeedAdapter(
            source_id=entry.source_id,
            feed_url=entry.url,
            source_name=entry.source_name,
            language=entry.language,
            categories=entry.categories,
            credibility_score=entry.credibility_score,
        )
    raise NotImplementedError(
        f"No adapter implemented for source_type={entry.source_type.value}"
    )


def _document_to_db(doc: Any, source_entry: SourceEntry) -> CanonicalDocumentDB:
    """Convert a CanonicalDocument domain object to its DB model."""
    from datetime import datetime
    from app.core.enums import AnalysisStatus

    return CanonicalDocumentDB(
        id=doc.id,
        source_id=doc.source_id,
        source_type=doc.source_type.value,
        external_id=getattr(doc, "external_id", None) or str(doc.id),
        url=doc.url,
        title=doc.title or "",
        author=getattr(doc, "author", "") or "",
        published_at=doc.published_at,
        language=doc.language.value if doc.language else "unknown",
        raw_text=doc.raw_text or "",
        cleaned_text=doc.cleaned_text or "",
        content_hash=doc.content_hash,
        is_duplicate=doc.is_duplicate,
        analysis_status=AnalysisStatus.PENDING.value,
        ingested_at=datetime.utcnow(),
    )


class IngestionRunner:
    """
    Coordinates the full pipeline: fetch → dedup → store.

    Args:
        session:   Async SQLAlchemy session (injected per request/task)
        registry:  SourceRegistry to look up source entries
    """

    def __init__(
        self,
        session: AsyncSession,
        registry: SourceRegistry | None = None,
    ) -> None:
        self._session = session
        self._registry = registry or get_registry()
        self._doc_repo = DocumentRepository(session)
        self._src_repo = SourceRepository(session)

    async def ingest_source(self, source_id: str) -> int:
        """
        Run full ingestion for a single source by ID.
        Returns number of new documents stored.

        Steps:
        1. Look up SourceEntry in registry
        2. Build adapter
        3. Fetch documents
        4. Deduplicate (in-memory session-level)
        5. Persist new documents
        6. Update source status (mark_fetched / mark_error)
        """
        entry = self._registry.get(source_id)
        if entry is None:
            logger.warning("ingest_source_not_found", source_id=source_id)
            return 0

        if not entry.is_fetchable:
            logger.info(
                "ingest_source_skipped",
                source_id=source_id,
                status=entry.status.value,
            )
            return 0

        if entry.source_type not in _SUPPORTED_TYPES:
            logger.warning(
                "ingest_source_unsupported_type",
                source_id=source_id,
                source_type=entry.source_type.value,
            )
            return 0

        logger.info("ingest_source_start", source_id=source_id, url=entry.url)

        try:
            adapter = _build_adapter(entry)
            async with adapter:
                fetch_result = await adapter.fetch()

            if not fetch_result.success:
                await self._src_repo.mark_error(source_id, fetch_result.error or "unknown")
                await self._session.commit()
                logger.warning(
                    "ingest_source_fetch_failed",
                    source_id=source_id,
                    error=fetch_result.error,
                )
                return 0

        except Exception as e:
            await self._src_repo.mark_error(source_id, str(e))
            await self._session.commit()
            logger.exception("ingest_source_error", source_id=source_id, error=str(e))
            return 0

        # Deduplication (in-memory)
        deduplicator = DocumentDeduplicator()
        unique_docs, _ = deduplicator.process_batch(fetch_result.documents)

        # Persist
        db_docs = [_document_to_db(doc, entry) for doc in unique_docs]
        new_count = await self._doc_repo.insert_many(db_docs)

        # Mark source as successfully fetched
        await self._src_repo.mark_fetched(source_id)
        await self._session.commit()

        logger.info(
            "ingest_source_complete",
            source_id=source_id,
            fetched=len(fetch_result.documents),
            unique=len(unique_docs),
            stored=new_count,
        )
        return new_count

    async def ingest_all(self, source_type: SourceType | None = None) -> dict[str, int]:
        """
        Ingest all fetchable sources from the registry.
        Optionally filter by source_type.

        Returns {source_id: new_document_count}.
        """
        sources = self._registry.fetchable()
        if source_type:
            sources = [s for s in sources if s.source_type == source_type]

        results: dict[str, int] = {}
        for entry in sources:
            count = await self.ingest_source(entry.source_id)
            results[entry.source_id] = count

        total = sum(results.values())
        logger.info(
            "ingest_all_complete",
            sources=len(results),
            total_new_documents=total,
        )
        return results


async def run_ingestion_for_source(
    source_id: str,
    session: AsyncSession,
    registry: SourceRegistry | None = None,
) -> int:
    """
    Convenience function: ingest a single source without instantiating the runner manually.
    Suitable for use in FastAPI background tasks or simple scripts.
    """
    runner = IngestionRunner(session=session, registry=registry)
    return await runner.ingest_source(source_id)
