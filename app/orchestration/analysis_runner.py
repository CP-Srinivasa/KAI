"""
Analysis Runner
===============
Orchestrates the document analysis pipeline:

  DocumentRepository.list_pending_analysis()
    → KeywordMatcher.match()              (rule-based pre-filter)
      → PriorityComposer.classify()       (pre-LLM priority score)
        → [optional] OpenAIProvider.analyze_document()
          → DocumentRepository.save_analysis()
            → DocumentRepository.mark_analysis_status()

Design decisions:
- Keyword + scoring always run (cheap, no API cost)
- LLM analysis is gated: min_keyword_score threshold + cost limit check
- Failed LLM calls → status=FAILED, document stays in DB
- Batch size configurable to avoid memory pressure
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.keywords.matcher import KeywordMatcher, MatchResult
from app.analysis.llm.base import BaseAnalysisProvider
from app.analysis.scoring.credibility import CredibilityScorer
from app.analysis.scoring.novelty import NoveltyScorer
from app.analysis.scoring.priority import PriorityComposer, ScoreBundle
from app.analysis.scoring.ranker import recency_score
from app.core.domain.document import AnalysisResult
from app.core.enums import AnalysisStatus, DocumentPriority
from app.core.errors import LLMCostLimitError, LLMError, LLMOutputValidationError
from app.core.logging import get_logger
from app.ingestion.source_registry import SourceRegistry, get_registry
from app.storage.models.db_models import CanonicalDocumentDB, DocumentAnalysisDB
from app.storage.repositories.document_repo import DocumentRepository

logger = get_logger(__name__)


def _db_doc_to_analysis_input(doc: CanonicalDocumentDB) -> dict[str, Any]:
    """Extract fields needed for analysis from DB model."""
    return {
        "id": str(doc.id),
        "title": doc.title or "",
        "cleaned_text": doc.cleaned_text or doc.raw_text or "",
        "url": doc.url or "",
        "source_id": doc.source_id,
        "published_at": doc.published_at,
    }


def _analysis_result_to_db(
    doc_id: Any,
    result: AnalysisResult,
    keyword_result: MatchResult,
) -> DocumentAnalysisDB:
    """Convert AnalysisResult + keyword hits to DocumentAnalysisDB record."""
    tags = list(set(result.tags + keyword_result.entity_hits))
    return DocumentAnalysisDB(
        document_id=doc_id,
        sentiment_label=result.sentiment_label.value if hasattr(result.sentiment_label, "value") else result.sentiment_label,
        sentiment_score=result.sentiment_score,
        relevance_score=result.relevance_score,
        impact_score=result.impact_score,
        confidence_score=result.confidence_score,
        novelty_score=result.novelty_score,
        credibility_score=result.credibility_score,
        spam_probability=result.spam_probability,
        market_scope=result.market_scope.value if hasattr(result.market_scope, "value") else result.market_scope,
        event_type=result.event_type.value if hasattr(result.event_type, "value") else result.event_type,
        recommended_priority=(
            result.recommended_priority.value
            if hasattr(result.recommended_priority, "value")
            else result.recommended_priority
        ),
        actionable=result.actionable,
        affected_assets=result.affected_assets,
        affected_sectors=result.affected_sectors,
        tags=tags,
        bull_case=result.bull_case,
        bear_case=result.bear_case,
        neutral_case=result.neutral_case,
        historical_analogs=result.historical_analogs,
        explanation_short=result.explanation_short,
        explanation_long=result.explanation_long,
        analyzed_by=result.analyzed_by,
        analysis_model=result.analysis_model,
        token_count=result.token_count,
        cost_usd=result.cost_usd,
        analyzed_at=result.analyzed_at or datetime.utcnow(),
    )


def _rule_based_analysis(
    doc: CanonicalDocumentDB,
    keyword_result: MatchResult,
    source_credibility: float,
    novelty_scorer: NoveltyScorer,
    credibility_scorer: CredibilityScorer,
    priority_composer: PriorityComposer,
) -> AnalysisResult:
    """
    Produce a rule-based AnalysisResult without LLM.
    Used when:
    - keyword score is below LLM threshold
    - LLM is not configured
    - Cost limit exceeded
    """
    from app.core.enums import EventType, MarketScope, SentimentLabel

    recency = recency_score(doc.published_at)
    credibility = credibility_scorer.score(
        source_credibility=source_credibility,
        title=doc.title or "",
        body=doc.cleaned_text or "",
    )
    novelty = novelty_scorer.score_and_register(doc.content_hash or "", doc.title or "")

    bundle = ScoreBundle(
        keyword_score=keyword_result.score,
        relevance_score=keyword_result.score,  # Keyword match as proxy for relevance
        impact_score=0.3,  # Conservative default
        recency_score=recency,
        credibility_score=credibility,
        novelty_score=novelty,
    )
    priority, composite_score = priority_composer.classify_with_score(bundle)

    return AnalysisResult(
        sentiment_label=SentimentLabel.NEUTRAL,
        sentiment_score=0.0,
        relevance_score=keyword_result.score,
        impact_score=0.3,
        confidence_score=0.3,  # Low confidence: rule-based only
        novelty_score=novelty,
        credibility_score=credibility,
        spam_probability=0.0,
        market_scope=MarketScope.UNKNOWN,
        affected_assets=keyword_result.entity_hits[:10],
        affected_sectors=[],
        event_type=EventType.UNKNOWN,
        bull_case="",
        bear_case="",
        neutral_case="",
        historical_analogs=[],
        recommended_priority=priority,
        actionable=keyword_result.has_entity_hit,
        tags=list(keyword_result.matched_keywords[:10]),
        explanation_short=f"Rule-based: {keyword_result.matched_count} keyword hits (score={keyword_result.score:.2f})",
        explanation_long="",
        analyzed_by="rule_engine",
        analyzed_at=datetime.utcnow(),
        analysis_model="",
        token_count=0,
        cost_usd=0.0,
    )


class AnalysisRunner:
    """
    Runs the full analysis pipeline: keyword filter → optional LLM → store results.

    Args:
        session:             Async DB session
        matcher:             KeywordMatcher (loaded from monitor/keywords.txt)
        llm_provider:        Optional LLM provider. If None, only rule-based analysis runs.
        registry:            SourceRegistry for credibility scores
        min_llm_score:       Minimum keyword score to trigger LLM analysis (default: 0.1)
        batch_size:          Max documents per run
    """

    def __init__(
        self,
        session: AsyncSession,
        matcher: KeywordMatcher,
        llm_provider: BaseAnalysisProvider | None = None,
        registry: SourceRegistry | None = None,
        min_llm_score: float = 0.10,
        batch_size: int = 50,
    ) -> None:
        self._session = session
        self._matcher = matcher
        self._llm = llm_provider
        self._registry = registry or get_registry()
        self._min_llm_score = min_llm_score
        self._batch_size = batch_size
        self._doc_repo = DocumentRepository(session)
        self._novelty = NoveltyScorer()
        self._credibility = CredibilityScorer()
        self._priority = PriorityComposer()

    def _source_credibility(self, source_id: str) -> float:
        entry = self._registry.get(source_id)
        return entry.credibility_score if entry else 0.5

    async def run(self, source_id: str | None = None) -> dict[str, int]:
        """
        Analyze a batch of pending documents.
        Returns summary: {analyzed, llm_used, rule_based, failed, skipped}.
        """
        pending = await self._doc_repo.list_pending_analysis(
            limit=self._batch_size,
            source_id=source_id,
        )

        if not pending:
            logger.info("analysis_runner_nothing_pending")
            return {"analyzed": 0, "llm_used": 0, "rule_based": 0, "failed": 0, "skipped": 0}

        logger.info("analysis_runner_start", batch_size=len(pending))
        stats = {"analyzed": 0, "llm_used": 0, "rule_based": 0, "failed": 0, "skipped": 0}

        for doc in pending:
            result = await self._analyze_one(doc, stats)

        await self._session.commit()
        logger.info("analysis_runner_complete", **stats)
        return stats

    async def _analyze_one(
        self,
        doc: CanonicalDocumentDB,
        stats: dict[str, int],
    ) -> None:
        """Analyze a single document. Updates stats in place."""
        await self._doc_repo.mark_analysis_status(doc.id, AnalysisStatus.IN_PROGRESS)

        # Step 1: Keyword matching (always)
        keyword_result = self._matcher.match_text(
            title=doc.title or "",
            body=doc.cleaned_text or doc.raw_text or "",
            url=doc.url or "",
        )

        source_credibility = self._source_credibility(doc.source_id)
        use_llm = (
            self._llm is not None
            and keyword_result.score >= self._min_llm_score
        )

        if use_llm:
            assert self._llm is not None
            try:
                # Build a minimal CanonicalDocument proxy for the provider
                from app.core.domain.document import CanonicalDocument
                from app.core.enums import Language, SourceType
                proxy = CanonicalDocument(
                    id=doc.id,
                    source_id=doc.source_id,
                    source_name=doc.source_name or "",
                    source_type=SourceType(doc.source_type) if doc.source_type else SourceType.RSS_FEED,
                    url=doc.url or "",
                    title=doc.title or "",
                    raw_text=doc.raw_text or "",
                    cleaned_text=doc.cleaned_text or "",
                    published_at=doc.published_at,
                    language=Language(doc.language) if doc.language else Language.UNKNOWN,
                )
                analysis = await self._llm.analyze_document(proxy)
                analysis.analyzed_by = f"{self._llm.provider_name}/{self._llm.model}"

                # Patch novelty and credibility from rule scorers (LLM may not compute these)
                if analysis.credibility_score == 0.5:  # Default untouched
                    analysis.credibility_score = self._credibility.score(
                        source_credibility=source_credibility,
                        title=doc.title or "",
                        body=doc.cleaned_text or "",
                    )
                self._novelty.register(doc.content_hash or "", doc.title or "")

                stats["llm_used"] += 1

            except LLMCostLimitError as e:
                logger.warning("analysis_cost_limit", doc_id=str(doc.id), error=str(e))
                analysis = _rule_based_analysis(
                    doc, keyword_result, source_credibility,
                    self._novelty, self._credibility, self._priority,
                )
                stats["rule_based"] += 1

            except (LLMError, LLMOutputValidationError) as e:
                logger.error("analysis_llm_error", doc_id=str(doc.id), error=str(e))
                await self._doc_repo.mark_analysis_status(doc.id, AnalysisStatus.FAILED)
                stats["failed"] += 1
                return

        else:
            analysis = _rule_based_analysis(
                doc, keyword_result, source_credibility,
                self._novelty, self._credibility, self._priority,
            )
            stats["rule_based"] += 1

        # Persist analysis
        db_analysis = _analysis_result_to_db(doc.id, analysis, keyword_result)
        await self._doc_repo.save_analysis(db_analysis)
        stats["analyzed"] += 1
