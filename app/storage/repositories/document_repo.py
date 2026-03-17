"""Document repository — persists and retrieves CanonicalDocument instances."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.domain.document import CanonicalDocument
from app.core.enums import DocumentType, SourceType
from app.core.errors import StorageError
from app.storage.models.document import CanonicalDocumentModel


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, doc: CanonicalDocument) -> CanonicalDocument:
        """Insert or update a document. Uses content_hash for conflict detection."""
        existing = await self.get_by_hash(doc.content_hash) if doc.content_hash else None
        if existing:
            return existing

        model = _to_model(doc)
        self._session.add(model)
        try:
            await self._session.flush()
        except Exception as e:
            raise StorageError(f"Failed to save document: {e}") from e
        return doc

    async def get_by_id(self, doc_id: str) -> CanonicalDocument | None:
        result = await self._session.execute(
            select(CanonicalDocumentModel).where(CanonicalDocumentModel.id == doc_id)
        )
        model = result.scalar_one_or_none()
        return _from_model(model) if model else None

    async def get_by_hash(self, content_hash: str) -> CanonicalDocument | None:
        result = await self._session.execute(
            select(CanonicalDocumentModel).where(
                CanonicalDocumentModel.content_hash == content_hash
            )
        )
        model = result.scalar_one_or_none()
        return _from_model(model) if model else None

    async def get_by_url(self, url: str) -> CanonicalDocument | None:
        result = await self._session.execute(
            select(CanonicalDocumentModel).where(CanonicalDocumentModel.url == url)
        )
        model = result.scalar_one_or_none()
        return _from_model(model) if model else None

    async def exists_by_hash(self, content_hash: str) -> bool:
        result = await self._session.execute(
            select(CanonicalDocumentModel.id).where(
                CanonicalDocumentModel.content_hash == content_hash
            )
        )
        return result.scalar_one_or_none() is not None

    async def list(
        self,
        source_id: str | None = None,
        source_type: SourceType | None = None,
        document_type: DocumentType | None = None,
        is_analyzed: bool | None = None,
        is_duplicate: bool | None = None,
        published_after: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CanonicalDocument]:
        stmt = select(CanonicalDocumentModel)
        if source_id:
            stmt = stmt.where(CanonicalDocumentModel.source_id == source_id)
        if source_type:
            stmt = stmt.where(CanonicalDocumentModel.source_type == source_type.value)
        if document_type:
            stmt = stmt.where(CanonicalDocumentModel.document_type == document_type.value)
        if is_analyzed is not None:
            stmt = stmt.where(CanonicalDocumentModel.is_analyzed == is_analyzed)
        if is_duplicate is not None:
            stmt = stmt.where(CanonicalDocumentModel.is_duplicate == is_duplicate)
        if published_after:
            stmt = stmt.where(CanonicalDocumentModel.published_at >= published_after)
        stmt = stmt.order_by(CanonicalDocumentModel.published_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return [_from_model(m) for m in result.scalars().all()]

    async def mark_duplicate(self, doc_id: str) -> None:
        await self._session.execute(
            update(CanonicalDocumentModel)
            .where(CanonicalDocumentModel.id == doc_id)
            .values(is_duplicate=True)
        )
        await self._session.flush()

    async def mark_analyzed(self, doc_id: str) -> None:
        await self._session.execute(
            update(CanonicalDocumentModel)
            .where(CanonicalDocumentModel.id == doc_id)
            .values(is_analyzed=True)
        )
        await self._session.flush()

    async def update_analysis(self, doc: CanonicalDocument) -> None:
        """Update a document with analysis scores, entities, and priority. Sets is_analyzed=True."""
        await self._session.execute(
            update(CanonicalDocumentModel)
            .where(CanonicalDocumentModel.id == str(doc.id))
            .values(
                sentiment_label=doc.sentiment_label.value if doc.sentiment_label else None,
                sentiment_score=doc.sentiment_score,
                relevance_score=doc.relevance_score,
                impact_score=doc.impact_score,
                credibility_score=doc.credibility_score,
                novelty_score=doc.novelty_score,
                spam_probability=doc.spam_probability,
                priority_score=doc.priority_score,
                market_scope=doc.market_scope.value,
                entity_mentions=[e.model_dump() for e in doc.entity_mentions],
                entities=doc.entities,
                tickers=doc.tickers,
                crypto_assets=doc.crypto_assets,
                people=doc.people,
                organizations=doc.organizations,
                tags=doc.tags,
                topics=doc.topics,
                categories=doc.categories,
                is_analyzed=True,
            )
        )
        await self._session.flush()


# ── Mapping helpers ───────────────────────────────────────────────────────────


def _to_model(doc: CanonicalDocument) -> CanonicalDocumentModel:
    return CanonicalDocumentModel(
        id=str(doc.id),
        external_id=doc.external_id,
        source_id=doc.source_id,
        source_name=doc.source_name,
        source_type=doc.source_type.value if doc.source_type else None,
        document_type=doc.document_type.value,
        provider=doc.provider,
        url=doc.url,
        title=doc.title,
        author=doc.author,
        language=doc.language,
        market_scope=doc.market_scope.value,
        published_at=doc.published_at,
        fetched_at=doc.fetched_at,
        raw_text=doc.raw_text,
        cleaned_text=doc.cleaned_text,
        summary=doc.summary,
        content_hash=doc.content_hash,
        sentiment_label=doc.sentiment_label.value if doc.sentiment_label else None,
        sentiment_score=doc.sentiment_score,
        relevance_score=doc.relevance_score,
        impact_score=doc.impact_score,
        novelty_score=doc.novelty_score,
        credibility_score=doc.credibility_score,
        spam_probability=doc.spam_probability,
        priority_score=doc.priority_score,
        is_duplicate=doc.is_duplicate,
        is_analyzed=doc.is_analyzed,
        entity_mentions=[e.model_dump() for e in doc.entity_mentions],
        entities=doc.entities,
        tickers=doc.tickers,
        crypto_assets=doc.crypto_assets,
        people=doc.people,
        organizations=doc.organizations,
        tags=doc.tags,
        topics=doc.topics,
        categories=doc.categories,
        youtube_meta=doc.youtube_meta.model_dump() if doc.youtube_meta else None,
        podcast_meta=doc.podcast_meta.model_dump() if doc.podcast_meta else None,
        document_metadata=doc.metadata,
    )


def _from_model(model: CanonicalDocumentModel) -> CanonicalDocument:
    from app.core.domain.document import EntityMention, PodcastEpisodeMeta, YouTubeVideoMeta

    entity_mentions = [EntityMention.model_validate(e) for e in (model.entity_mentions or [])]
    youtube_meta = (
        YouTubeVideoMeta.model_validate(model.youtube_meta) if model.youtube_meta else None
    )
    podcast_meta = (
        PodcastEpisodeMeta.model_validate(model.podcast_meta) if model.podcast_meta else None
    )

    return CanonicalDocument(
        id=model.id,  # type: ignore[arg-type]
        external_id=model.external_id,
        source_id=model.source_id,
        source_name=model.source_name,
        source_type=SourceType(model.source_type) if model.source_type else None,
        document_type=DocumentType(model.document_type),
        provider=model.provider,
        url=model.url,
        title=model.title,
        author=model.author,
        language=model.language,
        published_at=model.published_at,
        fetched_at=model.fetched_at,
        raw_text=model.raw_text,
        cleaned_text=model.cleaned_text,
        summary=model.summary,
        content_hash=model.content_hash,
        sentiment_label=(
            __import__("app.core.enums", fromlist=["SentimentLabel"]).SentimentLabel(
                model.sentiment_label
            )
            if model.sentiment_label
            else None
        ),
        sentiment_score=model.sentiment_score,
        relevance_score=model.relevance_score,
        impact_score=model.impact_score,
        novelty_score=model.novelty_score,
        credibility_score=model.credibility_score,
        spam_probability=model.spam_probability,
        priority_score=model.priority_score,
        is_duplicate=model.is_duplicate,
        is_analyzed=model.is_analyzed,
        entity_mentions=entity_mentions,
        entities=model.entities or [],
        tickers=model.tickers or [],
        crypto_assets=model.crypto_assets or [],
        people=model.people or [],
        organizations=model.organizations or [],
        tags=model.tags or [],
        topics=model.topics or [],
        categories=model.categories or [],
        youtube_meta=youtube_meta,
        podcast_meta=podcast_meta,
        metadata=model.document_metadata or {},
    )
