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

from collections.abc import Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.enums import SourceStatus, SourceType
from app.core.logging import get_logger
from app.ingestion.base.interfaces import FetchResult, SourceMetadata
from app.ingestion.rss.adapter import RSSFeedAdapter
from app.storage.repositories.source_repo import SourceRepository
from app.storage.schemas.source import SourceRead

logger = get_logger(__name__)


class RSSScheduler:
    """Schedules periodic RSS feed polling for all active RSS sources."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        interval_minutes: int = 15,
        on_result: Callable[[FetchResult], None] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._interval_minutes = interval_minutes
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
        metadata = SourceMetadata(
            source_id=source.source_id,
            source_name=source.provider or source.original_url,
            source_type=SourceType.RSS_FEED,
            url=url,
            status=source.status,
            provider=source.provider,
            notes=source.notes,
        )
        adapter = RSSFeedAdapter(metadata)
        result = await adapter.fetch()

        if result.success:
            logger.info(
                "rss_poll_success",
                source_id=source.source_id,
                doc_count=len(result.documents),
            )
        else:
            logger.warning(
                "rss_poll_failed",
                source_id=source.source_id,
                error=result.error,
            )

        if self._on_result:
            self._on_result(result)
