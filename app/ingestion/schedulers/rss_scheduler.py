"""RSS Feed Scheduler.

Polls all active RSS_FEED sources from the source registry at a
configurable interval using APScheduler.

Usage (attach to FastAPI lifespan):
    scheduler = RSSScheduler(session_factory, interval_minutes=15)
    scheduler.start()
    ...
    scheduler.stop()
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.enums import SourceStatus, SourceType
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.ingestion.base.interfaces import FetchResult
from app.ingestion.rss.service import collect_rss_feed
from app.storage.document_ingest import IngestPersistStats, persist_fetch_result
from app.storage.repositories.source_repo import SourceRepository
from app.storage.schemas.source import SourceRead

logger = get_logger(__name__)

PersistResultCallback = Callable[
    [FetchResult],
    Awaitable[IngestPersistStats | None] | IngestPersistStats | None,
]
ResultCallback = Callable[[FetchResult], Awaitable[object] | object]


class RSSScheduler:
    """Schedules periodic RSS feed polling for all active RSS sources."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        interval_minutes: int = 15,
        persist_result: PersistResultCallback | None = None,
        on_result: ResultCallback | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._interval_minutes = interval_minutes
        self._persist_result_callback = persist_result or self._persist_via_storage
        self._on_result = on_result
        self._scheduler = AsyncIOScheduler()

    def start(self) -> None:
        self._scheduler.add_job(
            self._poll_all,
            trigger="interval",
            minutes=self._interval_minutes,
            id="rss_poll",
            replace_existing=True,
            max_instances=1,
        )
        self._scheduler.start()
        logger.info(
            "rss_scheduler_started",
            interval_minutes=self._interval_minutes,
        )

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("rss_scheduler_stopped")

    async def _poll_all(self) -> None:
        """Fetch all active RSS sources. Called by scheduler."""
        async with self._session_factory() as session:
            repo = SourceRepository(session)
            sources = await repo.list(
                source_type=SourceType.RSS_FEED,
                status=SourceStatus.ACTIVE,
            )

        logger.info("rss_poll_started", source_count=len(sources))
        for source in sources:
            await self._poll_one(source)

    async def _poll_one(self, source: SourceRead) -> None:
        url = source.normalized_url or source.original_url
        settings = get_settings()
        collected = await collect_rss_feed(
            url=url,
            source_id=source.source_id,
            source_name=source.provider or source.original_url,
            monitor_dir=Path(settings.monitor_dir),
            timeout=settings.sources.fetch_timeout,
            max_retries=settings.sources.max_retries,
            status=source.status,
            provider=source.provider,
        )
        result = collected.fetch_result

        if result.success:
            logger.info(
                "rss_poll_success",
                source_id=source.source_id,
                doc_count=len(result.documents),
                classified_source_type=collected.classification.source_type.value,
            )
        else:
            logger.warning(
                "rss_poll_failed",
                source_id=source.source_id,
                error=result.error,
                classified_source_type=collected.classification.source_type.value,
            )

        await self._persist_result(result)
        await self._notify_result(result)

    async def _persist_via_storage(self, result: FetchResult) -> IngestPersistStats:
        return await persist_fetch_result(self._session_factory, result)

    async def _persist_result(self, result: FetchResult) -> IngestPersistStats | None:
        try:
            callback_result = self._persist_result_callback(result)
            if inspect.isawaitable(callback_result):
                stats = await callback_result
            else:
                stats = callback_result
        except Exception as err:
            logger.warning(
                "rss_poll_persist_failed",
                source_id=result.source_id,
                error=str(err),
            )
            return None

        if stats is None:
            return None

        logger.info(
            "rss_poll_persisted",
            source_id=result.source_id,
            fetched_count=stats.fetched_count,
            candidate_count=stats.candidate_count,
            batch_duplicates=stats.batch_duplicates,
            existing_duplicates=stats.existing_duplicates,
            saved_count=stats.saved_count,
            failed_count=stats.failed_count,
        )
        return stats

    async def _notify_result(self, result: FetchResult) -> None:
        if not self._on_result:
            return
        try:
            callback_result = self._on_result(result)
            if inspect.isawaitable(callback_result):
                await callback_result
        except Exception as err:
            logger.warning(
                "rss_poll_callback_failed",
                source_id=result.source_id,
                error=str(err),
            )
