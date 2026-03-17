"""Query DSL Executor — in-memory filter for CanonicalDocument lists.

Applies a QuerySpec against a list of CanonicalDocuments in memory.
Supports: text search, date range, source/document/market filters,
score thresholds, dedup exclusion, sorting, and pagination.

For DB-backed filtering use the DocumentRepository.list() instead.
This executor is for post-fetch filtering and pipeline-internal use.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.domain.document import CanonicalDocument, QuerySpec
from app.core.enums import SortBy


class QueryExecutor:
    """Filter and sort a list of CanonicalDocuments using a QuerySpec."""

    def execute(
        self,
        spec: QuerySpec,
        documents: list[CanonicalDocument],
    ) -> list[CanonicalDocument]:
        result = list(documents)

        # ── State filters ──────────────────────────────────────────────────────
        if spec.exclude_duplicates:
            result = [d for d in result if not d.is_duplicate]

        # ── Date filters ───────────────────────────────────────────────────────
        if spec.from_date:
            result = [d for d in result if d.published_at and d.published_at >= spec.from_date]
        if spec.to_date:
            result = [d for d in result if d.published_at and d.published_at <= spec.to_date]

        # ── Taxonomy filters ───────────────────────────────────────────────────
        if spec.source_types:
            result = [d for d in result if d.source_type in spec.source_types]
        if spec.document_types:
            result = [d for d in result if d.document_type in spec.document_types]
        if spec.market_scopes:
            result = [d for d in result if d.market_scope in spec.market_scopes]
        if spec.languages:
            result = [d for d in result if d.language in spec.languages]
        if spec.categories:
            result = [d for d in result if any(c in d.categories for c in spec.categories)]

        # ── Score filters ──────────────────────────────────────────────────────
        if spec.min_credibility is not None:
            result = [
                d for d in result
                if d.credibility_score is not None and d.credibility_score >= spec.min_credibility
            ]
        if spec.min_sentiment_abs is not None:
            result = [
                d for d in result
                if d.sentiment_score is not None
                and abs(d.sentiment_score) >= spec.min_sentiment_abs
            ]
        if spec.min_views is not None:
            result = [d for d in result if d.views is not None and d.views >= spec.min_views]
        if spec.min_clicks is not None:
            result = [d for d in result if d.clicks is not None and d.clicks >= spec.min_clicks]

        # ── Text filters ───────────────────────────────────────────────────────
        has_text_filter = any([
            spec.query_text,
            spec.include_terms,
            spec.exclude_terms,
            spec.any_terms,
            spec.all_terms,
            spec.exact_phrases,
            spec.title_terms,
            spec.meta_terms,
        ])
        if has_text_filter:
            result = [d for d in result if _text_matches(spec, d)]

        # ── Sort ───────────────────────────────────────────────────────────────
        result = _sort(result, spec.sort_by)

        # ── Pagination ─────────────────────────────────────────────────────────
        return result[spec.offset : spec.offset + spec.limit]


# ── Helpers ───────────────────────────────────────────────────────────────────

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _text_matches(spec: QuerySpec, doc: CanonicalDocument) -> bool:
    body = f"{doc.title} {doc.raw_text or ''} {doc.summary or ''}".lower()
    title = doc.title.lower()
    meta = str(doc.metadata).lower() if doc.metadata else ""

    if spec.query_text and spec.query_text.lower() not in body:
        return False
    for term in spec.include_terms:
        if term.lower() not in body:
            return False
    for term in spec.exclude_terms:
        if term.lower() in body:
            return False
    if spec.any_terms and not any(t.lower() in body for t in spec.any_terms):
        return False
    if spec.all_terms and not all(t.lower() in body for t in spec.all_terms):
        return False
    if spec.exact_phrases and not all(p.lower() in body for p in spec.exact_phrases):
        return False
    if spec.title_terms and not all(t.lower() in title for t in spec.title_terms):
        return False
    if spec.meta_terms and not all(t.lower() in meta for t in spec.meta_terms):
        return False
    return True


def _sort(docs: list[CanonicalDocument], sort_by: SortBy) -> list[CanonicalDocument]:
    if sort_by == SortBy.PUBLISHED_AT:
        return sorted(docs, key=lambda d: d.published_at or _EPOCH, reverse=True)
    if sort_by == SortBy.RELEVANCE:
        return sorted(docs, key=lambda d: d.relevance_score or 0.0, reverse=True)
    if sort_by == SortBy.IMPACT:
        return sorted(docs, key=lambda d: d.impact_score or 0.0, reverse=True)
    if sort_by == SortBy.SENTIMENT:
        return sorted(docs, key=lambda d: abs(d.sentiment_score or 0.0), reverse=True)
    if sort_by == SortBy.CREDIBILITY:
        return sorted(docs, key=lambda d: d.credibility_score or 0.0, reverse=True)
    return docs
