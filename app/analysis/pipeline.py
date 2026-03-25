"""Analysis pipeline for keyword, entity, LLM, and fallback analysis."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.analysis.base.interfaces import BaseAnalysisProvider, LLMAnalysisOutput
from app.analysis.keywords.engine import KeywordEngine, KeywordHit
from app.analysis.rules.rule_analyzer import compute_spam_probability
from app.core.domain.document import AnalysisResult, CanonicalDocument, EntityMention
from app.core.enums import AnalysisSource, MarketScope, SentimentLabel
from app.core.logging import get_logger
from app.normalization.entities import hits_to_entity_mentions

_MAX_CONCURRENT = 5  # max parallel LLM calls per run_batch()
_ASSET_HIT_CATEGORIES = frozenset({"crypto", "equity", "etf"})
_FALLBACK_MAX_TERMS = 20
_STUB_CONTENT_THRESHOLD = 50  # PH5C: skip LLM for docs with body â‰¤ 50 bytes
# PH4I: title-level crypto signal words for market_scope inference in fallback path
_CRYPTO_TITLE_TERMS = frozenset(
    {
        "bitcoin",
        "ethereum",
        "crypto",
        "defi",
        "blockchain",
        "nft",
        "altcoin",
        "stablecoin",
        "web3",
        "btc",
        "eth",
        "solana",
        "sol",
        "binance",
        "coinbase",
        "token",
        "wallet",
        "ledger",
    }
)

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


def _resolve_analysis_source(provider_name: str | None) -> AnalysisSource:
    if not provider_name:
        return AnalysisSource.RULE

    provider_name = provider_name.strip().lower()
    if provider_name in {"fallback", "rule"}:
        return AnalysisSource.RULE
    if provider_name in {"internal"}:
        return AnalysisSource.INTERNAL
    return AnalysisSource.EXTERNAL_LLM


def _resolve_runtime_provider_name(provider: BaseAnalysisProvider | None) -> str | None:
    if provider is None:
        return None

    normalized = provider.provider_name.strip()
    if normalized.startswith("ensemble("):
        active_provider_name = getattr(provider, "active_provider_name", None)
        if isinstance(active_provider_name, str):
            winner = active_provider_name.strip()
            if winner:
                return winner

        if isinstance(provider.model, str):
            winner = provider.model.strip()
            if winner:
                return winner

    return normalized or None


def _resolve_trace_metadata(provider: BaseAnalysisProvider | None) -> dict[str, object]:
    if provider is None:
        return {}

    provider_chain = getattr(provider, "provider_chain", None)
    chain: list[str] = []
    if isinstance(provider_chain, (list, tuple)):
        chain = [str(name).strip() for name in provider_chain if str(name).strip()]
    elif provider.provider_name.startswith("ensemble(") and provider.provider_name.endswith(")"):
        composite = provider.provider_name[len("ensemble(") : -1]
        chain = [name.strip() for name in composite.split(",") if name.strip()]

    if not chain:
        return {}

    return {"ensemble_chain": chain}


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
    raw = keyword_signal + entity_signal + metadata_signal
    # PH4G: minimum relevance floor for legitimate documents
    has_basic_signals = bool(document.title and (document.source_name or document.published_at))
    floor = 0.08 if has_basic_signals and raw == 0.0 else 0.0
    return round(min(1.0, max(raw, floor)), 4)


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

    # PH4I: enrich via structured asset fields and title keywords
    if document.crypto_assets:
        scores[MarketScope.CRYPTO] += len(document.crypto_assets)
    if document.tickers:
        scores[MarketScope.EQUITIES] += len(document.tickers)
    title_lower = (document.title or "").lower()
    if any(term in title_lower for term in _CRYPTO_TITLE_TERMS):
        scores[MarketScope.CRYPTO] += 1

    top_score = max(scores.values())
    if top_score == 0:
        return document.market_scope if document.market_scope != MarketScope.UNKNOWN else None

    ordered_scores = sorted(scores.values(), reverse=True)
    top_scope, _ = max(scores.items(), key=lambda item: item[1])
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
    trace_metadata: dict[str, object] = field(default_factory=dict)
    shadow_llm_output: LLMAnalysisOutput | None = None
    shadow_provider_name: str | None = None
    shadow_error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None

    def apply_to_document(self) -> None:
        """Apply analysis results, entities, and scores directly to the document."""
        self.document.entity_mentions = self.entity_mentions
        self.document.provider = self.provider_name
        if self.trace_metadata:
            self.document.metadata.update(self.trace_metadata)

        if self.shadow_llm_output is not None:
            self.document.metadata["shadow_analysis"] = self.shadow_llm_output.model_dump(
                mode="json", round_trip=True
            )
            self.document.metadata["shadow_provider"] = self.shadow_provider_name

        _sync_flat_entities(self.document, self.entity_mentions)

        if not self.analysis_result:
            return

        res = self.analysis_result
        spam_prob = self.llm_output.spam_probability if self.llm_output else res.spam_probability
        self.document.analysis_source = res.analysis_source

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

        self.document.metadata["explanation_short"] = res.explanation_short
        self.document.metadata["explanation_long"] = res.explanation_long


class AnalysisPipeline:
    """Run keyword, entity, and optional LLM analysis on one or many documents."""

    def __init__(
        self,
        keyword_engine: KeywordEngine,
        provider: BaseAnalysisProvider | None = None,
        run_llm: bool = True,
        shadow_provider: BaseAnalysisProvider | None = None,
    ) -> None:
        self._keyword_engine = keyword_engine
        self._provider = provider
        self._run_llm = run_llm
        self._shadow_provider = shadow_provider

    async def _run_shadow_analysis(
        self,
        doc: CanonicalDocument,
        *,
        text: str,
        context: dict[str, Any],
    ) -> tuple[LLMAnalysisOutput | None, str | None, str | None]:
        if self._shadow_provider is None:
            return None, None, None

        shadow_provider_name = (
            _resolve_runtime_provider_name(self._shadow_provider)
            or self._shadow_provider.provider_name
        )
        try:
            output = await self._shadow_provider.analyze(
                title=doc.title,
                text=text,
                context=context,
            )
        except Exception as exc:
            error = str(exc)
            logger.warning(
                "shadow_provider_failed",
                doc_id=str(doc.id),
                provider=shadow_provider_name,
                error=error,
            )
            return None, shadow_provider_name, error

        return output, shadow_provider_name, None

    async def run(self, doc: CanonicalDocument) -> PipelineResult:
        """Analyze a single document."""
        text = doc.cleaned_text or doc.raw_text or ""
        full_text = f"{doc.title} {text}".strip()

        keyword_hits = self._keyword_engine.match(full_text)
        entity_mentions = hits_to_entity_mentions(keyword_hits)

        llm_output: LLMAnalysisOutput | None = None
        analysis_result: AnalysisResult | None = None
        shadow_llm_output: LLMAnalysisOutput | None = None
        shadow_provider_name: str | None = None
        shadow_error: str | None = None
        provider_name = "fallback"
        trace_metadata = _resolve_trace_metadata(self._provider)
        context: dict[str, Any] = {
            "tickers": self._keyword_engine.match_tickers(full_text),
            "source_type": doc.source_type.value if doc.source_type else None,
        }

        fallback_reason: str | None = None
        if self._provider is None:
            fallback_reason = "LLM provider unavailable."
        elif not self._run_llm:
            fallback_reason = "LLM provider disabled."
        elif len(text) <= _STUB_CONTENT_THRESHOLD:
            # PH5C: skip LLM for stub/placeholder documents
            fallback_reason = "stub_document: content below threshold."
            logger.info(
                "stub_document_skipped_llm",
                doc_id=str(doc.id),
                content_len=len(text),
                threshold=_STUB_CONTENT_THRESHOLD,
            )
        elif (
            not keyword_hits
            and not doc.tickers
            and not doc.crypto_assets
        ):
            # D-109: relevance gate -- skip LLM for off-topic documents
            # that match zero keywords and have no pre-existing asset metadata.
            # These typically produce priority=1/relevance=0/scope=unknown.
            fallback_reason = "zero_relevance: no keyword or asset signals."
            logger.info(
                "zero_relevance_gate_skipped_llm",
                doc_id=str(doc.id),
                title=doc.title[:80] if doc.title else "",
            )

        if fallback_reason is not None:
            analysis_result = self._build_fallback_analysis(
                doc,
                text,
                keyword_hits,
                entity_mentions,
                fallback_reason=fallback_reason,
            )
            if self._shadow_provider is not None:
                (
                    shadow_llm_output,
                    shadow_provider_name,
                    shadow_error,
                ) = await self._run_shadow_analysis(
                    doc,
                    text=text,
                    context=context,
                )
        elif self._provider is not None:
            try:
                primary_task = asyncio.create_task(
                    self._provider.analyze(
                        title=doc.title,
                        text=text,
                        context=context,
                    )
                )

                shadow_task: (
                    asyncio.Task[tuple[LLMAnalysisOutput | None, str | None, str | None]] | None
                ) = None
                if self._shadow_provider is not None:
                    shadow_task = asyncio.create_task(
                        self._run_shadow_analysis(
                            doc,
                            text=text,
                            context=context,
                        )
                    )

                try:
                    primary_output = await primary_task
                except Exception:
                    if shadow_task is not None:
                        with contextlib.suppress(Exception):
                            await shadow_task
                    raise

                llm_output = primary_output
                if shadow_task is not None:
                    shadow_llm_output, shadow_provider_name, shadow_error = await shadow_task

                provider_name = (
                    _resolve_runtime_provider_name(self._provider) or self._provider.provider_name
                )
                analysis_source = _resolve_analysis_source(provider_name)

                analysis_result = AnalysisResult(
                    document_id=str(doc.id),
                    analysis_source=analysis_source,
                    sentiment_label=primary_output.sentiment_label,
                    sentiment_score=primary_output.sentiment_score,
                    relevance_score=primary_output.relevance_score,
                    impact_score=primary_output.impact_score,
                    confidence_score=primary_output.confidence_score,
                    novelty_score=primary_output.novelty_score,
                    market_scope=primary_output.market_scope,
                    affected_assets=primary_output.affected_assets,
                    affected_sectors=primary_output.affected_sectors,
                    event_type=primary_output.event_type,
                    explanation_short=primary_output.short_reasoning or "",
                    explanation_long=primary_output.long_reasoning or "",
                    actionable=primary_output.actionable,
                    tags=primary_output.tags,
                    spam_probability=primary_output.spam_probability,
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
            provider_name=provider_name,
            trace_metadata=trace_metadata,
            shadow_llm_output=shadow_llm_output,
            shadow_provider_name=shadow_provider_name,
            shadow_error=shadow_error,
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
        fallback_sectors = _unique_strings(document.categories)
        spam_probability = compute_spam_probability(document.title, text)
        relevance_score = _fallback_relevance(document, keyword_hits, entity_mentions)
        impact_score = _fallback_impact(document, affected_assets)
        novelty_score = _fallback_novelty(document)
        confidence_score = _fallback_confidence(keyword_hits, entity_mentions)
        market_scope = _fallback_market_scope(document, keyword_hits)

        # PH4J: enriched fallback tags from all available signals
        tag_sources: list[str] = (
            document.tags
            + document.topics
            + [hit.canonical for hit in keyword_hits]
            + [
                mention.name
                for mention in entity_mentions
                if mention.entity_type in {"topic", "person", "organization"}
            ]
            + document.categories
            + affected_assets
        )
        if document.source_name:
            tag_sources.append(document.source_name)
        if market_scope is not None and market_scope != MarketScope.UNKNOWN:
            tag_sources.append(market_scope.value)
        fallback_tags = _unique_strings(tag_sources)[:_FALLBACK_MAX_TERMS]
        # PH5C: inject stub_document tag for stub/placeholder documents
        if "stub_document" in fallback_reason.lower() and "stub_document" not in [
            t.lower() for t in fallback_tags
        ]:
            fallback_tags.insert(0, "stub_document")

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

        # PH4G note: actionable remains False in fallback to respect I-13
        # priority ceiling (max 5 for rule-only). Setting actionable=True would
        # trigger the +1 bonus in compute_priority() and breach the ceiling.

        return AnalysisResult(
            document_id=str(document.id),
            analysis_source=AnalysisSource.RULE,
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
