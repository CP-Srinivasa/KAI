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
from app.core.domain.document import AnalysisResult, CanonicalDocument
from app.core.enums import DocumentStatus, SourceStatus
from app.core.errors import StorageError
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.ingestion.rss.service import RSSCollectedFeed, collect_rss_feed
from app.market_data.service import create_market_data_adapter
from app.storage.document_ingest import persist_fetch_result
from app.storage.repositories.document_repo import DocumentRepository

logger = get_logger(__name__)

# ── D-119: Pipeline → Paper-Trade bridge ────────────────────────────────────
# After a directional alert dispatches successfully, trigger a paper-trade
# cycle so the PnL feedback loop is closed automatically.

_DIRECTIONAL_SENTIMENTS = frozenset({"bullish", "bearish"})


async def _maybe_trigger_paper_trade(
    doc: CanonicalDocument,
    analysis_result: AnalysisResult,
) -> bool:
    """Trigger a paper-trade cycle if the alert is directional-eligible.

    Returns True if a trade cycle was triggered, False otherwise.
    Fail-open: errors are logged but never propagate to the caller.
    """
    settings = get_settings()
    if not settings.operator.signal_auto_run_enabled:
        return False

    sentiment = (analysis_result.sentiment_label or "").strip().lower()
    if sentiment not in _DIRECTIONAL_SENTIMENTS:
        return False

    assets = list(analysis_result.affected_assets or [])
    if not assets:
        return False

    # Pick the first tradeable crypto asset
    from app.market_data.coingecko_adapter import _resolve_symbol

    symbol = None
    for asset in assets:
        resolved = _resolve_symbol(asset)
        if resolved is not None:
            symbol = resolved[0]  # e.g. "BTC/USDT"
            break

    if symbol is None:
        return False

    try:
        from app.orchestrator.trading_loop import run_trading_loop_once

        cycle = await run_trading_loop_once(
            symbol=symbol,
            mode=settings.operator.signal_auto_run_mode,
            analysis_result=analysis_result,
            freshness_threshold_seconds=300.0,
        )
        logger.info(
            "pipeline_paper_trade_triggered",
            doc_id=str(doc.id),
            symbol=symbol,
            sentiment=sentiment,
            cycle_id=cycle.cycle_id,
            cycle_status=(
                cycle.status.value if hasattr(cycle.status, "value") else str(cycle.status)
            ),
        )
        return True
    except Exception as exc:
        logger.warning(
            "pipeline_paper_trade_failed",
            doc_id=str(doc.id),
            symbol=symbol,
            error=str(exc),
        )
        return False


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
    alerts_fired_count: int = 0
    priority_distribution: dict[int, int] = field(default_factory=dict)
    top_results: list[PipelineResult] = field(default_factory=list)


async def collect_feed_for_pipeline(
    *,
    url: str,
    source_id: str,
    source_name: str,
    monitor_dir: str | Path,
    timeout: int,
    max_retries: int,
    status: SourceStatus = SourceStatus.ACTIVE,
    provider: str | None = None,
) -> RSSCollectedFeed:
    """Canonical feed collection entrypoint for pipeline-facing callers."""
    return await collect_rss_feed(
        url=url,
        source_id=source_id,
        source_name=source_name,
        monitor_dir=Path(monitor_dir),
        timeout=timeout,
        max_retries=max_retries,
        status=status,
        provider=provider,
    )


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
    collected = await collect_feed_for_pipeline(
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
            alerts_fired_count=0,
            priority_distribution={},
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
            alerts_fired_count=0,
            priority_distribution={},
        )

    # ── Stage 3: Analyze ────────────────────────────────────────────────────
    market_adapter = create_market_data_adapter(provider="coingecko")
    pipeline = AnalysisPipeline(
        keyword_engine=keyword_engine,
        provider=provider,
        run_llm=provider is not None,
        shadow_provider=shadow_provider,
        market_data_adapter=market_adapter,
    )
    pipeline_results = await pipeline.run_batch(saved_docs)

    # ── Stage 4+5: Apply + Update ───────────────────────────────────────────
    analyzed_count = 0
    failed_count = ingest_stats.failed_count
    alerts_fired_count = 0
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
                        deliveries = await alert_service.process_document(
                            res.document, res.analysis_result, spam_probability=spam_prob
                        )
                        if deliveries:
                            alerts_fired_count += 1
                            await _maybe_trigger_paper_trade(
                                res.document, res.analysis_result
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
                    deliveries = await alert_service.process_document(
                        res.document, res.analysis_result, spam_probability=spam_prob
                    )
                    if deliveries:
                        alerts_fired_count += 1

    priority_distribution: dict[int, int] = {}
    for res in pipeline_results:
        if not res.success:
            continue
        score = res.document.priority_score
        if score is None:
            continue
        priority_distribution[score] = priority_distribution.get(score, 0) + 1

    logger.info(
        "pipeline_complete",
        url=url,
        fetched=ingest_stats.fetched_count,
        saved=ingest_stats.saved_count,
        analyzed=analyzed_count,
        failed=failed_count,
        alerts_fired=alerts_fired_count,
        priority_distribution=priority_distribution,
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
        alerts_fired_count=alerts_fired_count,
        priority_distribution=priority_distribution,
        top_results=top_results,
    )


async def run_youtube_pipeline(
    channel_url: str,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    keyword_engine: KeywordEngine,
    provider: BaseAnalysisProvider | None = None,
    shadow_provider: BaseAnalysisProvider | None = None,
    api_key: str,
    source_id: str = "youtube",
    source_name: str = "YouTube",
    max_results: int = 5,
    timeout: int = 15,
    dry_run: bool = False,
) -> PipelineRunStats:
    """Fetch a YouTube channel's recent videos, persist, analyze, and alert.

    Drop-in equivalent of run_rss_pipeline for YouTube channels.
    Uses YouTube Data API v3 + transcript extraction.
    """
    from app.ingestion.youtube.adapter import fetch_youtube_channel

    # Stage 1: Fetch via YouTube API
    fetch_result = await fetch_youtube_channel(
        api_key,
        channel_url,
        source_id=source_id,
        source_name=source_name,
        max_results=max_results,
        timeout=timeout,
    )

    if not fetch_result.success:
        logger.warning("youtube_pipeline_fetch_failed", url=channel_url, error=fetch_result.error)
        return PipelineRunStats(
            source_id=source_id,
            url=channel_url,
            fetched_count=0,
            saved_count=0,
            analyzed_count=0,
            failed_count=1,
            skipped_count=0,
            alerts_fired_count=0,
            priority_distribution={},
        )

    # Stage 2: Persist (reuse standard dedup pipeline)
    ingest_stats = await persist_fetch_result(
        session_factory,
        fetch_result,
        dry_run=dry_run,
    )
    saved_docs = ingest_stats.preview_documents
    skipped = ingest_stats.batch_duplicates + ingest_stats.existing_duplicates

    logger.info(
        "youtube_pipeline_persisted",
        url=channel_url,
        fetched=ingest_stats.fetched_count,
        saved=ingest_stats.saved_count,
        skipped=skipped,
    )

    if not saved_docs:
        return PipelineRunStats(
            source_id=source_id,
            url=channel_url,
            fetched_count=ingest_stats.fetched_count,
            saved_count=ingest_stats.saved_count,
            analyzed_count=0,
            failed_count=ingest_stats.failed_count,
            skipped_count=skipped,
            alerts_fired_count=0,
            priority_distribution={},
        )

    # Stage 3-5: Analyze + Alert (identical to RSS pipeline)
    market_adapter = create_market_data_adapter(provider="coingecko")
    pipeline = AnalysisPipeline(
        keyword_engine=keyword_engine,
        provider=provider,
        run_llm=provider is not None,
        shadow_provider=shadow_provider,
        market_data_adapter=market_adapter,
    )
    pipeline_results = await pipeline.run_batch(saved_docs)

    analyzed_count = 0
    failed_count = ingest_stats.failed_count
    alerts_fired_count = 0
    alert_service = AlertService.from_settings(get_settings())

    if not dry_run:
        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            for res in pipeline_results:
                if not res.success:
                    failed_count += 1
                    try:
                        await repo.update_status(str(res.document.id), DocumentStatus.FAILED)
                    except StorageError:
                        pass
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
                    if res.analysis_result is not None:
                        spam_prob = (
                            res.llm_output.spam_probability
                            if res.llm_output
                            else res.analysis_result.spam_probability
                        )
                        deliveries = await alert_service.process_document(
                            res.document, res.analysis_result, spam_probability=spam_prob
                        )
                        if deliveries:
                            alerts_fired_count += 1
                            await _maybe_trigger_paper_trade(
                                res.document, res.analysis_result
                            )
                except StorageError as exc:
                    logger.warning(
                        "youtube_pipeline_update_failed",
                        doc_id=str(res.document.id),
                        error=str(exc),
                    )
                    failed_count += 1
    else:
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
                    deliveries = await alert_service.process_document(
                        res.document, res.analysis_result, spam_probability=spam_prob
                    )
                    if deliveries:
                        alerts_fired_count += 1

    priority_distribution: dict[int, int] = {}
    for res in pipeline_results:
        if not res.success:
            continue
        score = res.document.priority_score
        if score is None:
            continue
        priority_distribution[score] = priority_distribution.get(score, 0) + 1

    top_results = sorted(
        pipeline_results,
        key=lambda r: r.document.priority_score or 0,
        reverse=True,
    )

    return PipelineRunStats(
        source_id=source_id,
        url=channel_url,
        fetched_count=ingest_stats.fetched_count,
        saved_count=ingest_stats.saved_count,
        analyzed_count=analyzed_count,
        failed_count=failed_count,
        skipped_count=skipped,
        alerts_fired_count=alerts_fired_count,
        priority_distribution=priority_distribution,
        top_results=top_results,
    )


async def run_newsdata_pipeline(
    query: str,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    keyword_engine: KeywordEngine,
    provider: BaseAnalysisProvider | None = None,
    shadow_provider: BaseAnalysisProvider | None = None,
    api_key: str,
    source_id: str = "newsdata",
    source_name: str = "NewsData.io",
    language: str = "en",
    category: str | None = "business",
    size: int = 10,
    dry_run: bool = False,
) -> PipelineRunStats:
    """Fetch articles from NewsData.io, persist, analyze, and alert.

    Drop-in equivalent of run_rss_pipeline for the NewsData.io API.
    """
    from app.integrations.newsdata.client import NewsdataClient

    client = NewsdataClient(api_key=api_key)

    # Stage 1: Fetch via NewsData.io API
    try:
        articles = await client.fetch_latest(
            q=query,
            language=language,
            category=category,
            size=size,
        )
    except Exception as exc:
        logger.warning("newsdata_pipeline_fetch_failed", query=query, error=str(exc))
        return PipelineRunStats(
            source_id=source_id,
            url=f"newsdata:{query}",
            fetched_count=0,
            saved_count=0,
            analyzed_count=0,
            failed_count=1,
            skipped_count=0,
            alerts_fired_count=0,
            priority_distribution={},
        )

    # Convert articles to CanonicalDocuments via FetchResult
    from datetime import UTC, datetime

    from app.core.domain.document import CanonicalDocument
    from app.core.enums import DocumentType, SourceType
    from app.ingestion.base.interfaces import FetchResult

    documents: list[CanonicalDocument] = []
    for article in articles:
        raw_text = article.content or article.description or ""
        authors = ", ".join(article.creator) if article.creator else None
        documents.append(
            CanonicalDocument(
                external_id=article.article_id,
                source_id=source_id,
                source_name=source_name,
                source_type=SourceType.NEWS_API,
                document_type=DocumentType.ARTICLE,
                provider="newsdata",
                url=article.link,
                title=article.title,
                raw_text=raw_text,
                published_at=article.published_at,
                fetched_at=datetime.now(UTC),
                author=authors,
                metadata={
                    "newsdata_source_id": article.source_id,
                    "source_url": article.source_url,
                    "source_priority": article.source_priority,
                    "language": article.language,
                    "categories": article.category,
                    "countries": article.country,
                    "keywords": article.keywords,
                },
            )
        )

    fetch_result = FetchResult(
        source_id=source_id,
        documents=documents,
        fetched_at=datetime.now(UTC),
        success=True,
    )

    logger.info(
        "newsdata_pipeline_fetched",
        query=query,
        articles=len(articles),
    )

    # Stage 2: Persist
    ingest_stats = await persist_fetch_result(
        session_factory,
        fetch_result,
        dry_run=dry_run,
    )
    saved_docs = ingest_stats.preview_documents
    skipped = ingest_stats.batch_duplicates + ingest_stats.existing_duplicates

    if not saved_docs:
        return PipelineRunStats(
            source_id=source_id,
            url=f"newsdata:{query}",
            fetched_count=ingest_stats.fetched_count,
            saved_count=ingest_stats.saved_count,
            analyzed_count=0,
            failed_count=ingest_stats.failed_count,
            skipped_count=skipped,
            alerts_fired_count=0,
            priority_distribution={},
        )

    # Stage 3-5: Analyze + Alert
    market_adapter = create_market_data_adapter(provider="coingecko")
    pipeline = AnalysisPipeline(
        keyword_engine=keyword_engine,
        provider=provider,
        run_llm=provider is not None,
        shadow_provider=shadow_provider,
        market_data_adapter=market_adapter,
    )
    pipeline_results = await pipeline.run_batch(saved_docs)

    analyzed_count = 0
    failed_count = ingest_stats.failed_count
    alerts_fired_count = 0
    alert_service = AlertService.from_settings(get_settings())

    if not dry_run:
        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            for res in pipeline_results:
                if not res.success:
                    failed_count += 1
                    try:
                        await repo.update_status(str(res.document.id), DocumentStatus.FAILED)
                    except StorageError:
                        pass
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
                    if res.analysis_result is not None:
                        spam_prob = (
                            res.llm_output.spam_probability
                            if res.llm_output
                            else res.analysis_result.spam_probability
                        )
                        deliveries = await alert_service.process_document(
                            res.document, res.analysis_result, spam_probability=spam_prob
                        )
                        if deliveries:
                            alerts_fired_count += 1
                            await _maybe_trigger_paper_trade(
                                res.document, res.analysis_result
                            )
                except StorageError as exc:
                    logger.warning(
                        "newsdata_pipeline_update_failed",
                        doc_id=str(res.document.id),
                        error=str(exc),
                    )
                    failed_count += 1
    else:
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
                    deliveries = await alert_service.process_document(
                        res.document, res.analysis_result, spam_probability=spam_prob
                    )
                    if deliveries:
                        alerts_fired_count += 1

    priority_distribution: dict[int, int] = {}
    for res in pipeline_results:
        if not res.success:
            continue
        score = res.document.priority_score
        if score is None:
            continue
        priority_distribution[score] = priority_distribution.get(score, 0) + 1

    top_results = sorted(
        pipeline_results,
        key=lambda r: r.document.priority_score or 0,
        reverse=True,
    )

    return PipelineRunStats(
        source_id=source_id,
        url=f"newsdata:{query}",
        fetched_count=ingest_stats.fetched_count,
        saved_count=ingest_stats.saved_count,
        analyzed_count=analyzed_count,
        failed_count=failed_count,
        skipped_count=skipped,
        alerts_fired_count=alerts_fired_count,
        priority_distribution=priority_distribution,
        top_results=top_results,
    )


async def run_twitter_pipeline(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    keyword_engine: KeywordEngine,
    provider: BaseAnalysisProvider | None = None,
    shadow_provider: BaseAnalysisProvider | None = None,
    bearer_token: str,
    handles: list[str] | None = None,
    monitor_path: str = "monitor/social_accounts.txt",
    max_per_user: int = 5,
    source_id: str = "twitter",
    source_name: str = "X/Twitter",
    dry_run: bool = False,
) -> PipelineRunStats:
    """Fetch tweets from watchlist handles, persist, analyze, and alert.

    If *handles* is None, reads from monitor/social_accounts.txt.
    """
    from app.integrations.twitter.client import TwitterClient

    if handles is None:
        handles = _load_twitter_handles(monitor_path)
    if not handles:
        return PipelineRunStats(
            source_id=source_id, url="twitter:watchlist",
            fetched_count=0, saved_count=0, analyzed_count=0,
            failed_count=0, skipped_count=0, alerts_fired_count=0,
            priority_distribution={},
        )

    client = TwitterClient(bearer_token=bearer_token)
    try:
        tweets = await client.fetch_watchlist_tweets(handles, max_per_user=max_per_user)
    except Exception as exc:
        logger.warning("twitter_pipeline_fetch_failed", error=str(exc))
        return PipelineRunStats(
            source_id=source_id, url="twitter:watchlist",
            fetched_count=0, saved_count=0, analyzed_count=0,
            failed_count=1, skipped_count=0, alerts_fired_count=0,
            priority_distribution={},
        )

    from datetime import UTC, datetime

    from app.core.enums import DocumentType, SourceType
    from app.ingestion.base.interfaces import FetchResult

    documents: list[CanonicalDocument] = []
    for tweet in tweets:
        text = tweet.text
        title = f"@{tweet.author_username}: {text[:80]}{'...' if len(text) > 80 else ''}"
        documents.append(
            CanonicalDocument(
                external_id=tweet.tweet_id,
                source_id=source_id,
                source_name=source_name,
                source_type=SourceType.SOCIAL_API,
                document_type=DocumentType.ARTICLE,
                provider="twitter",
                url=f"https://x.com/{tweet.author_username}/status/{tweet.tweet_id}",
                title=title,
                raw_text=text,
                published_at=tweet.created_at,
                fetched_at=datetime.now(UTC),
                author=f"@{tweet.author_username} ({tweet.author_name})",
                metadata={
                    "tweet_id": tweet.tweet_id,
                    "author_id": tweet.author_id,
                    "lang": tweet.lang,
                    "like_count": tweet.like_count,
                    "retweet_count": tweet.retweet_count,
                    "reply_count": tweet.reply_count,
                    "quote_count": tweet.quote_count,
                    "impression_count": tweet.impression_count,
                    "hashtags": tweet.hashtags,
                    "cashtags": tweet.cashtags,
                },
            )
        )

    fetch_result = FetchResult(
        source_id=source_id,
        documents=documents,
        fetched_at=datetime.now(UTC),
        success=True,
    )
    logger.info("twitter_pipeline_fetched", handles=len(handles), tweets=len(tweets))

    ingest_stats = await persist_fetch_result(session_factory, fetch_result, dry_run=dry_run)
    saved_docs = ingest_stats.preview_documents
    skipped = ingest_stats.batch_duplicates + ingest_stats.existing_duplicates

    if not saved_docs:
        return PipelineRunStats(
            source_id=source_id, url="twitter:watchlist",
            fetched_count=ingest_stats.fetched_count,
            saved_count=ingest_stats.saved_count,
            analyzed_count=0, failed_count=ingest_stats.failed_count,
            skipped_count=skipped, alerts_fired_count=0,
            priority_distribution={},
        )

    market_adapter = create_market_data_adapter(provider="coingecko")
    pipeline = AnalysisPipeline(
        keyword_engine=keyword_engine,
        provider=provider,
        run_llm=provider is not None,
        shadow_provider=shadow_provider,
        market_data_adapter=market_adapter,
    )
    pipeline_results = await pipeline.run_batch(saved_docs)

    analyzed_count = 0
    failed_count = ingest_stats.failed_count
    alerts_fired_count = 0
    alert_service = AlertService.from_settings(get_settings())

    if not dry_run:
        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            for res in pipeline_results:
                if not res.success:
                    failed_count += 1
                    try:
                        await repo.update_status(str(res.document.id), DocumentStatus.FAILED)
                    except StorageError:
                        pass
                    continue
                res.apply_to_document()
                try:
                    if res.analysis_result is not None:
                        await repo.update_analysis(
                            str(res.document.id), res.analysis_result,
                            provider_name=res.document.provider,
                            metadata_updates=res.document.metadata,
                        )
                    else:
                        await repo.update_status(str(res.document.id), DocumentStatus.ANALYZED)
                    analyzed_count += 1
                    if res.analysis_result is not None:
                        spam_prob = (
                            res.llm_output.spam_probability
                            if res.llm_output else res.analysis_result.spam_probability
                        )
                        deliveries = await alert_service.process_document(
                            res.document, res.analysis_result, spam_probability=spam_prob,
                        )
                        if deliveries:
                            alerts_fired_count += 1
                            await _maybe_trigger_paper_trade(res.document, res.analysis_result)
                except StorageError as exc:
                    logger.warning("twitter_pipeline_update_failed",
                                   doc_id=str(res.document.id), error=str(exc))
                    failed_count += 1
    else:
        for res in pipeline_results:
            if res.success:
                res.apply_to_document()
                analyzed_count += 1

    priority_distribution: dict[int, int] = {}
    for res in pipeline_results:
        if not res.success:
            continue
        score = res.document.priority_score
        if score is not None:
            priority_distribution[score] = priority_distribution.get(score, 0) + 1

    top_results = sorted(
        pipeline_results,
        key=lambda r: r.document.priority_score or 0,
        reverse=True,
    )

    return PipelineRunStats(
        source_id=source_id, url="twitter:watchlist",
        fetched_count=ingest_stats.fetched_count,
        saved_count=ingest_stats.saved_count,
        analyzed_count=analyzed_count, failed_count=failed_count,
        skipped_count=skipped, alerts_fired_count=alerts_fired_count,
        priority_distribution=priority_distribution,
        top_results=top_results,
    )


def _load_twitter_handles(path: str) -> list[str]:
    """Read @handles from monitor/social_accounts.txt (twitter lines only)."""
    from pathlib import Path as _Path

    p = _Path(path)
    if not p.exists():
        return []
    handles: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) >= 2 and parts[0].strip().lower() == "twitter":
            handle = parts[1].strip().lstrip("@")
            if handle:
                handles.append(handle)
    return handles
