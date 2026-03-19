"""Analysis Pipeline — orchestrates keyword matching, entity extraction, and LLM analysis.

Pipeline stages:
  1. Keyword matching     — KeywordEngine.match(title + text)
  2. Entity extraction    — hits_to_entity_mentions(hits)
  3. LLM analysis         — BaseAnalysisProvider.analyze() (optional, skipped if no provider)
  4. AnalysisResult       — assembled from LLMAnalysisOutput + document linkage

The pipeline is intentionally stateless after construction.
Each run() call is independent and safe to call concurrently.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.analysis.base.interfaces import BaseAnalysisProvider, LLMAnalysisOutput
from app.analysis.keywords.engine import KeywordEngine, KeywordHit
from app.core.domain.document import AnalysisResult, CanonicalDocument, EntityMention
from app.enrichment.entities.matcher import hits_to_entity_mentions

_MAX_CONCURRENT = 5  # max parallel LLM calls per run_batch()


@dataclass
class PipelineResult:
    document: CanonicalDocument
    keyword_hits: list[KeywordHit] = field(default_factory=list)
    entity_mentions: list[EntityMention] = field(default_factory=list)
    llm_output: LLMAnalysisOutput | None = None
    analysis_result: AnalysisResult | None = None
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None

    def apply_to_document(self) -> None:
        """Applies analysis results, entities and blended scores directly to the document."""
        self.document.entity_mentions = self.entity_mentions

        # Sync simple entities
        for ent in self.entity_mentions:
            if ent.entity_type == "topic" and ent.name not in self.document.topics:
                self.document.topics.append(ent.name)

        if not self.analysis_result or not self.llm_output:
            return

        res = self.analysis_result
        spam_prob = self.llm_output.spam_probability
        self.document.sentiment_label = res.sentiment_label
        self.document.sentiment_score = res.sentiment_score
        self.document.impact_score = res.impact_score
        self.document.credibility_score = 1.0 - spam_prob
        self.document.novelty_score = res.novelty_score
        self.document.spam_probability = spam_prob
        if res.market_scope is not None:
            self.document.market_scope = res.market_scope
        else:
            self.document.market_scope = self.llm_output.market_scope

        # Merge structured extras
        self.document.tags = list(set(self.document.tags + res.tags))
        self.document.tickers = list(set(self.document.tickers + res.affected_assets))
        self.document.categories = list(set(self.document.categories + res.affected_sectors))

        from app.analysis.scoring import calculate_final_relevance, compute_priority
        # Calculate Blended Relevance
        blended = calculate_final_relevance(res.relevance_score, self.keyword_hits)
        self.document.relevance_score = blended
        res.relevance_score = blended

        # Compute Priority
        priority = compute_priority(res, spam_probability=spam_prob)
        self.document.priority_score = priority.priority
        res.recommended_priority = priority.priority
        res.spam_probability = spam_prob


class AnalysisPipeline:
    """Run keyword + entity + LLM analysis on one or many documents.

    Args:
        keyword_engine: Required. Used for stages 1 + 2.
        provider:       Optional. When None, LLM stage is skipped.
        run_llm:        Toggle LLM stage without removing the provider.
    """

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

        # Stage 1 — keyword matching
        keyword_hits = self._keyword_engine.match(full_text)

        # Stage 2 — entity extraction
        entity_mentions = hits_to_entity_mentions(keyword_hits)

        # Stage 3 — LLM analysis (optional)
        llm_output: LLMAnalysisOutput | None = None
        analysis_result: AnalysisResult | None = None

        if self._run_llm and self._provider:
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
                )
            except Exception as exc:
                return PipelineResult(
                    document=doc,
                    keyword_hits=keyword_hits,
                    entity_mentions=entity_mentions,
                    error=str(exc),
                )

        return PipelineResult(
            document=doc,
            keyword_hits=keyword_hits,
            entity_mentions=entity_mentions,
            llm_output=llm_output,
            analysis_result=analysis_result,
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
