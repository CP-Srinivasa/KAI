"""
Document Search and Retrieval Endpoints
========================================
GET  /documents/{document_id}           — fetch document by ID
GET  /documents/{document_id}/analysis  — fetch analysis for document
POST /documents/search                  — full QuerySpec search
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.query.parser import QueryParser
from app.core.query.schema import QuerySpec, SortBy
from app.core.errors import QueryParseError
from app.storage.db.session import get_db_session
from app.storage.models.db_models import CanonicalDocumentDB, DocumentAnalysisDB

router = APIRouter()


def _doc_to_dict(doc: CanonicalDocumentDB) -> dict:
    return {
        "id": str(doc.id),
        "source_id": doc.source_id,
        "source_name": doc.source_name,
        "source_type": doc.source_type,
        "url": doc.url,
        "title": doc.title,
        "author": doc.author,
        "published_at": doc.published_at.isoformat() if doc.published_at else None,
        "language": doc.language,
        "analysis_status": doc.analysis_status,
        "is_duplicate": doc.is_duplicate,
        "categories": doc.categories,
        "tags": doc.tags,
    }


def _analysis_to_dict(a: DocumentAnalysisDB) -> dict:
    return {
        "id": str(a.id),
        "document_id": str(a.document_id),
        "sentiment_label": a.sentiment_label,
        "sentiment_score": a.sentiment_score,
        "relevance_score": a.relevance_score,
        "impact_score": a.impact_score,
        "confidence_score": a.confidence_score,
        "novelty_score": a.novelty_score,
        "credibility_score": a.credibility_score,
        "spam_probability": a.spam_probability,
        "market_scope": a.market_scope,
        "event_type": a.event_type,
        "recommended_priority": a.recommended_priority,
        "actionable": a.actionable,
        "affected_assets": a.affected_assets,
        "affected_sectors": a.affected_sectors,
        "tags": a.tags,
        "bull_case": a.bull_case,
        "bear_case": a.bear_case,
        "neutral_case": a.neutral_case,
        "historical_analogs": a.historical_analogs,
        "explanation_short": a.explanation_short,
        "analyzed_by": a.analyzed_by,
        "analysis_model": a.analysis_model,
        "token_count": a.token_count,
        "cost_usd": a.cost_usd,
        "analyzed_at": a.analyzed_at.isoformat() if a.analyzed_at else None,
    }


@router.post("/search")
async def search_documents(
    spec: QuerySpec,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Search documents using QuerySpec.
    Parses query_text as Boolean DSL if query_mode=boolean.
    Other filters (date, source, language) applied in SQL.

    Note: In-memory DSL filtering applied after SQL pre-fetch for complex queries.
    """
    from sqlalchemy import and_

    stmt = select(CanonicalDocumentDB).where(
        CanonicalDocumentDB.is_duplicate.is_(False)
    )

    # Apply SQL-translatable filters
    if spec.from_date:
        stmt = stmt.where(CanonicalDocumentDB.published_at >= spec.from_date)
    if spec.to_date:
        stmt = stmt.where(CanonicalDocumentDB.published_at <= spec.to_date)
    if spec.languages:
        lang_values = [lang.value for lang in spec.languages]
        stmt = stmt.where(CanonicalDocumentDB.language.in_(lang_values))
    if spec.source_ids:
        stmt = stmt.where(CanonicalDocumentDB.source_id.in_(spec.source_ids))
    if spec.source_types:
        type_values = [st.value for st in spec.source_types]
        stmt = stmt.where(CanonicalDocumentDB.source_type.in_(type_values))

    # Sorting
    sort_col_map = {
        SortBy.PUBLISHED_AT: CanonicalDocumentDB.published_at,
        SortBy.FETCHED_AT: CanonicalDocumentDB.fetched_at,
    }
    sort_col = sort_col_map.get(spec.sort_by, CanonicalDocumentDB.published_at)
    stmt = stmt.order_by(
        sort_col.desc().nullslast() if spec.sort_descending else sort_col.asc().nullsfirst()
    )

    # Pre-fetch with a larger limit for in-memory filtering
    prefetch_limit = min(spec.limit * 5, 1000)
    stmt = stmt.limit(prefetch_limit)

    result = await session.execute(stmt)
    docs = list(result.scalars().all())

    # In-memory DSL filter (query_text)
    if spec.query_text.strip():
        try:
            from app.core.query.executor import QueryExecutor
            from app.core.domain.document import CanonicalDocument
            from app.core.enums import Language, SourceType
            ast = QueryParser().parse(spec.query_text)
            executor = QueryExecutor(ast)

            # Build lightweight proxy objects for the executor
            filtered = []
            for doc in docs:
                try:
                    proxy = CanonicalDocument(
                        id=doc.id,
                        source_id=doc.source_id,
                        source_name=doc.source_name or "",
                        source_type=SourceType(doc.source_type),
                        url=doc.url or "",
                        title=doc.title or "",
                        raw_text=doc.raw_text or "",
                        cleaned_text=doc.cleaned_text or "",
                        published_at=doc.published_at,
                        language=Language(doc.language) if doc.language else Language.UNKNOWN,
                        metadata=dict(doc.metadata_ or {}),
                    )
                    if executor.matches(proxy):
                        filtered.append(doc)
                except Exception:
                    pass  # Skip unparseable docs
            docs = filtered
        except QueryParseError as e:
            raise HTTPException(status_code=400, detail=f"Query parse error: {e}")

    total = len(docs)
    paginated = docs[spec.offset: spec.offset + spec.limit]

    return {
        "documents": [_doc_to_dict(d) for d in paginated],
        "total": total,
        "offset": spec.offset,
        "limit": spec.limit,
        "query": spec.to_display(),
    }


@router.get("/{document_id}")
async def get_document(
    document_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Fetch a single document by UUID."""
    try:
        doc_uuid = UUID(document_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid document UUID")

    result = await session.execute(
        select(CanonicalDocumentDB).where(CanonicalDocumentDB.id == doc_uuid)
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found")

    data = _doc_to_dict(doc)
    data["summary"] = doc.summary
    data["fetched_at"] = doc.fetched_at.isoformat() if doc.fetched_at else None
    return data


@router.get("/{document_id}/analysis")
async def get_document_analysis(
    document_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Fetch the LLM/rule-based analysis for a document."""
    try:
        doc_uuid = UUID(document_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid document UUID")

    result = await session.execute(
        select(DocumentAnalysisDB).where(DocumentAnalysisDB.document_id == doc_uuid)
    )
    analysis = result.scalar_one_or_none()

    if analysis is None:
        # Check if document exists at all
        doc_result = await session.execute(
            select(CanonicalDocumentDB.analysis_status).where(
                CanonicalDocumentDB.id == doc_uuid
            )
        )
        status = doc_result.scalar_one_or_none()
        if status is None:
            raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
        return {
            "document_id": document_id,
            "analysis": None,
            "analysis_status": status,
        }

    return {
        "document_id": document_id,
        "analysis": _analysis_to_dict(analysis),
        "analysis_status": "completed",
    }
