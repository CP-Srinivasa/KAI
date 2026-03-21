"""End-to-End Pipeline Service: Fetch → Persist → Analyze → Update.

Single entry point for the core data pipeline loop.
Called from CLI commands, the RSS scheduler, and future API triggers.

Stages:
  1. collect_rss_feed()        → FetchResult with CanonicalDocuments
  2. persist_fetch_result()    → saves new docs, deduplicates, returns saved list
  3. AnalysisPipeline.run_batch() → PipelineResult per saved doc
  4. apply_to_document()       → merges scores + priority onto each doc
  5. update_analysis()         → writes scores to DB, sets is_analyzed=True
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.service import AlertService
from app.analysis.base.interfaces import BaseAnalysisProvider
from app.analysis.keywords.engine import KeywordEngine
from app.analysis.pipeline import AnalysisPipeline, PipelineResult
from app.core.enums import DocumentStatus
from app.core.errors import StorageError
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.ingestion.rss.service import collect_rss_feed
from app.storage.document_ingest import persist_fetch_result
from app.storage.repositories.document_repo import DocumentRepository

logger = get_logger(__name__)


@dataclass(frozen=True)
class PipelineRunStats:
    """Summary of a single end-to-end pipeline run."""

    source_id: str
    url: str
    fetched_count: int
    saved_count: int
    analyzed_count: int
    failed_count: int
    skipped_count: int  # batch + existing duplicates
    top_results: list[PipelineResult] = field(default_factory=list)


async def run_rss_pipeline(
    url: str,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    keyword_engine: KeywordEngine,
    provider: BaseAnalysisProvider | None = None,
    shadow_provider: BaseAnalysisProvider | None = None,
    source_id: str = "manual",
    source_name: str = "Manual",
    monitor_dir: str | Path = "monitor",
    timeout: int = 15,
    max_retries: int = 3,
    dry_run: bool = False,
) -> PipelineRunStats:
    """Fetch an RSS feed, persist new docs, and analyze them in one shot.

    In dry_run mode:
    - No documents are written to DB
    - Analysis still runs so results can be previewed
    - session_factory is not used
    """
    monitor_dir = Path(monitor_dir)

    # ── Stage 1: Fetch ──────────────────────────────────────────────────────
    collected = await collect_rss_feed(
        url=url,
        source_id=source_id,
        source_name=source_name,
        monitor_dir=monitor_dir,
        timeout=timeout,
        max_retries=max_retries,
    )
    fetch_result = collected.fetch_result

    if not fetch_result.success:
        logger.warning("pipeline_fetch_failed", url=url, error=fetch_result.error)
        return PipelineRunStats(
            source_id=source_id,
            url=url,
            fetched_count=0,
            saved_count=0,
            analyzed_count=0,
            failed_count=1,
            skipped_count=0,
        )

    # ── Stage 2: Persist ────────────────────────────────────────────────────
    ingest_stats = await persist_fetch_result(
        session_factory,
        fetch_result,
        dry_run=dry_run,
    )
    saved_docs = ingest_stats.preview_documents
    skipped = ingest_stats.batch_duplicates + ingest_stats.existing_duplicates

    logger.info(
        "pipeline_persisted",
        url=url,
        fetched=ingest_stats.fetched_count,
        saved=ingest_stats.saved_count,
        skipped=skipped,
        dry_run=dry_run,
    )

    if not saved_docs:
        return PipelineRunStats(
            source_id=source_id,
            url=url,
            fetched_count=ingest_stats.fetched_count,
            saved_count=ingest_stats.saved_count,
            analyzed_count=0,
            failed_count=ingest_stats.failed_count,
            skipped_count=skipped,
        )

    # ── Stage 3: Analyze ────────────────────────────────────────────────────
    pipeline = AnalysisPipeline(
        keyword_engine=keyword_engine,
        provider=provider,
        run_llm=provider is not None,
        shadow_provider=shadow_provider,
    )
    pipeline_results = await pipeline.run_batch(saved_docs)

    # ── Stage 4+5: Apply + Update ───────────────────────────────────────────
    analyzed_count = 0
    failed_count = ingest_stats.failed_count
    alert_service = AlertService.from_settings(get_settings())

    if not dry_run:
        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            for res in pipeline_results:
                if not res.success:
                    logger.warning(
                        "pipeline_analysis_failed",
                        doc_id=str(res.document.id),
                        error=res.error,
                    )
                    failed_count += 1
                    try:
                        await repo.update_status(str(res.document.id), DocumentStatus.FAILED)
                    except StorageError:
                        pass  # best-effort — do not mask the original error
                    continue
                res.apply_to_document()
                try:
                    if res.analysis_result is not None:
                        await repo.update_analysis(
                            str(res.document.id),
                            res.analysis_result,
                            provider_name=res.document.provider,
                            metadata_updates=res.document.metadata,
                        )
                    else:
                        await repo.update_status(str(res.document.id), DocumentStatus.ANALYZED)
                    analyzed_count += 1
                    # Dispatch alert — only when analysis result is available
                    if res.analysis_result is not None:
                        spam_prob = (
                            res.llm_output.spam_probability
                            if res.llm_output
                            else res.analysis_result.spam_probability
                        )
                        await alert_service.process_document(
                            res.document, res.analysis_result, spam_probability=spam_prob
                        )
                except StorageError as exc:
                    logger.warning(
                        "pipeline_update_failed",
                        doc_id=str(res.document.id),
                        error=str(exc),
                    )
                    failed_count += 1
                    try:
                        await repo.update_status(str(res.document.id), DocumentStatus.FAILED)
                    except StorageError:
                        pass  # best-effort
    else:
        # Dry-run: apply scores for display, skip DB write
        for res in pipeline_results:
            if res.success:
                res.apply_to_document()
                analyzed_count += 1
                if res.analysis_result is not None:
                    spam_prob = (
                        res.llm_output.spam_probability
                        if res.llm_output
                        else res.analysis_result.spam_probability
                    )
                    await alert_service.process_document(
                        res.document, res.analysis_result, spam_probability=spam_prob
                    )

    logger.info(
        "pipeline_complete",
        url=url,
        fetched=ingest_stats.fetched_count,
        saved=ingest_stats.saved_count,
        analyzed=analyzed_count,
        failed=failed_count,
    )

    top_results = sorted(
        pipeline_results,
        key=lambda r: r.document.priority_score or 0,
        reverse=True,
    )

    return PipelineRunStats(
        source_id=source_id,
        url=url,
        fetched_count=ingest_stats.fetched_count,
        saved_count=ingest_stats.saved_count,
        analyzed_count=analyzed_count,
        failed_count=failed_count,
        skipped_count=skipped,
        top_results=top_results,
    )
