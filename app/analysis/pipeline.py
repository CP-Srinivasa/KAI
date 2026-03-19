"""Analysis pipeline for keyword, entity, LLM, and fallback analysis."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.analysis.base.interfaces import BaseAnalysisProvider, LLMAnalysisOutput
from app.analysis.keywords.engine import KeywordEngine, KeywordHit
from app.analysis.rules.rule_analyzer import compute_spam_probability
from app.core.domain.document import AnalysisResult, CanonicalDocument, EntityMention
from app.core.enums import MarketScope, SentimentLabel
from app.core.logging import get_logger
from app.enrichment.entities.matcher import hits_to_entity_mentions

_MAX_CONCURRENT = 5  # max parallel LLM calls per run_batch()
_ASSET_HIT_CATEGORIES = frozenset({"crypto", "equity", "etf"})
_FALLBACK_MAX_TERMS = 20

logger = get_logger(__name__)


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        key = normalized.lower()
        if not normalized or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def _fallback_relevance(
    document: CanonicalDocument,
    keyword_hits: list[KeywordHit],
    entity_mentions: list[EntityMention],
) -> float:
    keyword_signal = sum(min(hit.occurrences, 3) for hit in keyword_hits) * 0.12
    entity_signal = min(0.18, len(entity_mentions) * 0.05)
    metadata_values = (
        document.tags
        + document.topics
        + document.categories
        + document.tickers
        + document.crypto_assets
        + document.people
        + document.organizations
    )
    metadata_signal = min(0.12, len(_unique_strings(metadata_values)) * 0.03)
    return round(min(1.0, keyword_signal + entity_signal + metadata_signal), 4)


def _fallback_impact(document: CanonicalDocument, affected_assets: list[str]) -> float:
    asset_signal = min(0.2, len(affected_assets) * 0.08)
    category_signal = min(0.1, len(_unique_strings(document.categories)) * 0.03)
    source_signal = 0.05 if document.source_type is not None else 0.0
    return round(min(0.35, asset_signal + category_signal + source_signal), 4)


def _fallback_novelty(document: CanonicalDocument) -> float:
    if document.published_at is None:
        return 0.35

    reference_time = document.fetched_at or datetime.now(UTC)
    age_hours = max(
        0.0,
        (reference_time - document.published_at).total_seconds() / 3600,
    )
    if age_hours <= 24:
        return 0.6
    if age_hours <= 24 * 7:
        return 0.45
    return 0.25


def _fallback_confidence(
    keyword_hits: list[KeywordHit],
    entity_mentions: list[EntityMention],
) -> float:
    return round(min(0.75, 0.35 + len(keyword_hits) * 0.06 + len(entity_mentions) * 0.04), 4)


def _fallback_market_scope(
    document: CanonicalDocument,
    keyword_hits: list[KeywordHit],
) -> MarketScope | None:
    scores: dict[MarketScope, int] = {
        MarketScope.CRYPTO: 0,
        MarketScope.EQUITIES: 0,
        MarketScope.MACRO: 0,
    }
    for hit in keyword_hits:
        if hit.category == "crypto":
            scores[MarketScope.CRYPTO] += hit.occurrences
        elif hit.category in {"equity", "etf"}:
            scores[MarketScope.EQUITIES] += hit.occurrences
        elif hit.category == "macro":
            scores[MarketScope.MACRO] += hit.occurrences

    if document.market_scope in scores:
        scores[document.market_scope] += 1

    top_score = max(scores.values())
    if top_score == 0:
        return document.market_scope if document.market_scope != MarketScope.UNKNOWN else None

    ordered_scores = sorted(scores.values(), reverse=True)
    top_scope = max(scores, key=scores.get)
    if ordered_scores[1] and ordered_scores[1] >= top_score * 0.75:
        return MarketScope.MIXED
    return top_scope


def _sync_flat_entities(document: CanonicalDocument, entity_mentions: list[EntityMention]) -> None:
    for mention in entity_mentions:
        name = mention.name.strip()
        if not name:
            continue
        if mention.entity_type == "topic" and name not in document.topics:
            document.topics.append(name)
        elif mention.entity_type == "person":
            if name not in document.people:
                document.people.append(name)
            if name not in document.entities:
                document.entities.append(name)
        elif mention.entity_type == "organization":
            if name not in document.organizations:
                document.organizations.append(name)
            if name not in document.entities:
                document.entities.append(name)


@dataclass
class PipelineResult:
    document: CanonicalDocument
    keyword_hits: list[KeywordHit] = field(default_factory=list)
    entity_mentions: list[EntityMention] = field(default_factory=list)
    llm_output: LLMAnalysisOutput | None = None
    analysis_result: AnalysisResult | None = None
    error: str | None = None
    provider_name: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None

    def apply_to_document(self) -> None:
        """Apply analysis results, entities, and scores directly to the document."""
        self.document.entity_mentions = self.entity_mentions
        self.document.provider = self.provider_name
        _sync_flat_entities(self.document, self.entity_mentions)

        if not self.analysis_result:
            return

        res = self.analysis_result
        spam_prob = self.llm_output.spam_probability if self.llm_output else res.spam_probability

        self.document.sentiment_label = res.sentiment_label
        self.document.sentiment_score = res.sentiment_score
        self.document.impact_score = res.impact_score
        self.document.credibility_score = 1.0 - spam_prob
        self.document.novelty_score = res.novelty_score
        self.document.spam_probability = spam_prob

        if res.market_scope is not None:
            self.document.market_scope = res.market_scope
        elif self.llm_output is not None:
            self.document.market_scope = self.llm_output.market_scope

        self.document.tags = _unique_strings(self.document.tags + res.tags)
        self.document.tickers = _unique_strings(self.document.tickers + res.affected_assets)
        self.document.categories = _unique_strings(self.document.categories + res.affected_sectors)

        from app.analysis.scoring import calculate_final_relevance, compute_priority

        blended_relevance = (
            calculate_final_relevance(res.relevance_score, self.keyword_hits)
            if self.llm_output is not None
            else res.relevance_score
        )
        self.document.relevance_score = blended_relevance
        res.relevance_score = blended_relevance

        priority = compute_priority(res, spam_probability=spam_prob)
        self.document.priority_score = priority.priority
        res.recommended_priority = priority.priority
        res.spam_probability = spam_prob


class AnalysisPipeline:
    """Run keyword, entity, and optional LLM analysis on one or many documents."""

    def __init__(
        self,
        keyword_engine: KeywordEngine,
        provider: BaseAnalysisProvider | None = None,
        run_llm: bool = True,
    ) -> None:
        self._keyword_engine = keyword_engine
        self._provider = provider
        self._run_llm = run_llm

    async def run(self, doc: CanonicalDocument) -> PipelineResult:
        """Analyze a single document."""
        text = doc.cleaned_text or doc.raw_text or ""
        full_text = f"{doc.title} {text}".strip()

        keyword_hits = self._keyword_engine.match(full_text)
        entity_mentions = hits_to_entity_mentions(keyword_hits)

        llm_output: LLMAnalysisOutput | None = None
        analysis_result: AnalysisResult | None = None

        fallback_reason: str | None = None
        if self._provider is None:
            fallback_reason = "LLM provider unavailable."
        elif not self._run_llm:
            fallback_reason = "LLM provider disabled."

        if fallback_reason is not None:
            analysis_result = self._build_fallback_analysis(
                doc,
                text,
                keyword_hits,
                entity_mentions,
                fallback_reason=fallback_reason,
            )
        elif self._provider is not None:
            context: dict[str, Any] = {
                "tickers": self._keyword_engine.match_tickers(full_text),
                "source_type": doc.source_type.value if doc.source_type else None,
            }
            try:
                llm_output = await self._provider.analyze(
                    title=doc.title,
                    text=text,
                    context=context,
                )
                analysis_result = AnalysisResult(
                    document_id=str(doc.id),
                    sentiment_label=llm_output.sentiment_label,
                    sentiment_score=llm_output.sentiment_score,
                    relevance_score=llm_output.relevance_score,
                    impact_score=llm_output.impact_score,
                    confidence_score=llm_output.confidence_score,
                    novelty_score=llm_output.novelty_score,
                    market_scope=llm_output.market_scope,
                    affected_assets=llm_output.affected_assets,
                    affected_sectors=llm_output.affected_sectors,
                    event_type=llm_output.event_type,
                    explanation_short=llm_output.short_reasoning or "",
                    explanation_long=llm_output.long_reasoning or "",
                    actionable=llm_output.actionable,
                    tags=llm_output.tags,
                    spam_probability=llm_output.spam_probability,
                )
            except Exception as exc:
                logger.warning(
                    "analysis_provider_failed_fallback",
                    doc_id=str(doc.id),
                    provider=self._provider.provider_name,
                    error=str(exc),
                )
                analysis_result = self._build_fallback_analysis(
                    doc,
                    text,
                    keyword_hits,
                    entity_mentions,
                    fallback_reason="LLM provider call failed.",
                )

        return PipelineResult(
            document=doc,
            keyword_hits=keyword_hits,
            entity_mentions=entity_mentions,
            llm_output=llm_output,
            analysis_result=analysis_result,
            provider_name=self._provider.provider_name if self._provider else "fallback",
        )

    def _build_fallback_analysis(
        self,
        document: CanonicalDocument,
        text: str,
        keyword_hits: list[KeywordHit],
        entity_mentions: list[EntityMention],
        *,
        fallback_reason: str,
    ) -> AnalysisResult:
        affected_assets = _unique_strings(
            [hit.canonical for hit in keyword_hits if hit.category in _ASSET_HIT_CATEGORIES]
            + document.tickers
            + document.crypto_assets
        )
        fallback_tags = _unique_strings(
            document.tags
            + document.topics
            + [hit.canonical for hit in keyword_hits]
            + [
                mention.name
                for mention in entity_mentions
                if mention.entity_type in {"topic", "person", "organization"}
            ]
        )[:_FALLBACK_MAX_TERMS]
        fallback_sectors = _unique_strings(document.categories)
        spam_probability = compute_spam_probability(document.title, text)
        relevance_score = _fallback_relevance(document, keyword_hits, entity_mentions)
        impact_score = _fallback_impact(document, affected_assets)
        novelty_score = _fallback_novelty(document)
        confidence_score = _fallback_confidence(keyword_hits, entity_mentions)
        market_scope = _fallback_market_scope(document, keyword_hits)

        keyword_terms = ", ".join(hit.canonical for hit in keyword_hits[:5])
        entity_terms = ", ".join(mention.name for mention in entity_mentions[:5])
        details = [fallback_reason]
        if keyword_terms:
            details.append(f"keywords: {keyword_terms}")
        if entity_terms:
            details.append(f"entities: {entity_terms}")
        if affected_assets:
            details.append(f"assets: {', '.join(affected_assets[:5])}")
        if document.source_name:
            details.append(f"source: {document.source_name}")

        return AnalysisResult(
            document_id=str(document.id),
            sentiment_label=SentimentLabel.NEUTRAL,
            sentiment_score=0.0,
            relevance_score=relevance_score,
            impact_score=impact_score,
            confidence_score=confidence_score,
            novelty_score=novelty_score,
            market_scope=market_scope,
            affected_assets=affected_assets,
            affected_sectors=fallback_sectors,
            explanation_short=f"Rule-based fallback analysis. {fallback_reason}",
            explanation_long=" ".join(details),
            actionable=False,
            tags=fallback_tags,
            spam_probability=spam_probability,
        )

    async def run_batch(
        self,
        documents: list[CanonicalDocument],
    ) -> list[PipelineResult]:
        """Analyze multiple documents with bounded concurrency."""
        semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

        async def _bounded(doc: CanonicalDocument) -> PipelineResult:
            async with semaphore:
                return await self.run(doc)

        return list(await asyncio.gather(*[_bounded(d) for d in documents]))
