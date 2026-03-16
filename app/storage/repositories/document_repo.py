"""
Document Repository
===================
CRUD operations for CanonicalDocument and DocumentAnalysis records.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AnalysisStatus
from app.core.logging import get_logger
from app.storage.models.db_models import CanonicalDocumentDB, DocumentAnalysisDB

logger = get_logger(__name__)


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, doc_id: UUID) -> CanonicalDocumentDB | None:
        result = await self._session.execute(
            select(CanonicalDocumentDB).where(CanonicalDocumentDB.id == doc_id)
        )
        return result.scalar_one_or_none()

    async def get_by_content_hash(self, content_hash: str) -> CanonicalDocumentDB | None:
        result = await self._session.execute(
            select(CanonicalDocumentDB).where(CanonicalDocumentDB.content_hash == content_hash)
        )
        return result.scalar_one_or_none()

    async def exists(self, content_hash: str) -> bool:
        return await self.get_by_content_hash(content_hash) is not None

    async def insert(self, doc: CanonicalDocumentDB) -> CanonicalDocumentDB:
        self._session.add(doc)
        logger.debug("document_inserted", doc_id=str(doc.id), title=doc.title[:60])
        return doc

    async def insert_many(self, docs: list[CanonicalDocumentDB]) -> int:
        """Insert multiple documents. Returns count of actually inserted (skipping duplicates)."""
        inserted = 0
        for doc in docs:
            if not await self.exists(doc.content_hash):
                self._session.add(doc)
                inserted += 1
        logger.info("documents_bulk_insert", attempted=len(docs), inserted=inserted)
        return inserted

    async def list_pending_analysis(
        self, limit: int = 50, source_id: str | None = None
    ) -> list[CanonicalDocumentDB]:
        """Return documents awaiting LLM analysis, ordered by recency."""
        stmt = (
            select(CanonicalDocumentDB)
            .where(
                CanonicalDocumentDB.analysis_status == AnalysisStatus.PENDING.value,
                CanonicalDocumentDB.is_duplicate.is_(False),
            )
            .order_by(CanonicalDocumentDB.published_at.desc().nullslast())
            .limit(limit)
        )
        if source_id:
            stmt = stmt.where(CanonicalDocumentDB.source_id == source_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def mark_analysis_status(self, doc_id: UUID, status: AnalysisStatus) -> None:
        await self._session.execute(
            update(CanonicalDocumentDB)
            .where(CanonicalDocumentDB.id == doc_id)
            .values(analysis_status=status.value)
        )

    async def save_analysis(self, analysis: DocumentAnalysisDB) -> DocumentAnalysisDB:
        self._session.add(analysis)
        await self._session.execute(
            update(CanonicalDocumentDB)
            .where(CanonicalDocumentDB.id == analysis.document_id)
            .values(analysis_status=AnalysisStatus.COMPLETED.value)
        )
        logger.debug("analysis_saved", doc_id=str(analysis.document_id))
        return analysis

    async def count_by_source(self, source_id: str) -> int:
        from sqlalchemy import func
        result = await self._session.execute(
            select(func.count()).where(CanonicalDocumentDB.source_id == source_id)
        )
        return result.scalar_one() or 0
