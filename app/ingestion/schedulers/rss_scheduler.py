"""
RSS Ingestion Scheduler
=======================
APScheduler-based scheduler that periodically fetches all active RSS/podcast
feeds from the SourceRegistry and stores new documents.

Design:
- One APScheduler job per configurable interval
- Fetches all fetchable RSS+podcast sources from registry
- Runs adapters concurrently (asyncio.gather with semaphore)
- Deduplication and storage handled by IngestionRunner
- Graceful error handling: single failed source does not block others
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.enums import SourceType
from app.core.logging import get_logger
from app.ingestion.source_registry import SourceRegistry, get_registry

if TYPE_CHECKING:
    from app.orchestration.ingestion_runner import IngestionRunner

logger = get_logger(__name__)

_RSS_SOURCE_TYPES = {SourceType.RSS_FEED, SourceType.PODCAST_FEED}


class RSSScheduler:
    """
    Schedules periodic RSS/podcast feed ingestion jobs.

    Usage:
        scheduler = RSSScheduler(runner=runner, registry=registry)
        scheduler.start(interval_minutes=15)
        # ... later ...
        scheduler.stop()
    """

    def __init__(
        self,
        runner: "IngestionRunner",
        registry: SourceRegistry | None = None,
        max_concurrent: int = 10,
    ) -> None:
        self._runner = runner
        self._registry = registry or get_registry()
        self._max_concurrent = max_concurrent
        self._scheduler = AsyncIOScheduler()
        self._last_run: datetime | None = None
        self._running = False

    def start(self, interval_minutes: int = 15) -> None:
        """Start the scheduler with the given interval."""
        if self._running:
            logger.warning("rss_scheduler_already_running")
            return

        self._scheduler.add_job(
            self._run_cycle,
            trigger=IntervalTrigger(minutes=interval_minutes),
            id="rss_ingestion",
            name="RSS Feed Ingestion",
            replace_existing=True,
            max_instances=1,  # Prevent overlapping runs
        )
        self._scheduler.start()
        self._running = True
        logger.info(
            "rss_scheduler_started",
            interval_minutes=interval_minutes,
            max_concurrent=self._max_concurrent,
        )

    def stop(self) -> None:
        """Stop the scheduler gracefully."""
        if self._running:
            self._scheduler.shutdown(wait=False)
            self._running = False
            logger.info("rss_scheduler_stopped")

    async def run_now(self) -> dict[str, int]:
        """Trigger an immediate ingestion cycle (for CLI / on-demand use)."""
        return await self._run_cycle()

    async def _run_cycle(self) -> dict[str, int]:
        """
        Core ingestion cycle: fetch all active RSS+podcast sources concurrently.
        Returns summary dict with counts.
        """
        fetchable = [
            s for s in self._registry.fetchable()
            if s.source_type in _RSS_SOURCE_TYPES
        ]

        if not fetchable:
            logger.info("rss_scheduler_no_sources")
            return {"sources": 0, "documents_new": 0, "errors": 0}

        logger.info("rss_scheduler_cycle_start", sources=len(fetchable))

        semaphore = asyncio.Semaphore(self._max_concurrent)
        results = await asyncio.gather(
            *[self._fetch_one(source, semaphore) for source in fetchable],
            return_exceptions=True,
        )

        summary = {"sources": len(fetchable), "documents_new": 0, "errors": 0}
        for result in results:
            if isinstance(result, Exception):
                summary["errors"] += 1
            elif isinstance(result, dict):
                summary["documents_new"] += result.get("new", 0)
                if result.get("error"):
                    summary["errors"] += 1

        self._last_run = datetime.utcnow()
        logger.info("rss_scheduler_cycle_complete", **summary)
        return summary

    async def _fetch_one(
        self,
        source: "app.ingestion.source_registry.SourceEntry",  # type: ignore[name-defined]
        semaphore: asyncio.Semaphore,
    ) -> dict[str, int | str | None]:
        async with semaphore:
            try:
                count = await self._runner.ingest_source(source.source_id)
                return {"new": count, "error": None}
            except Exception as e:
                logger.error(
                    "rss_scheduler_source_error",
                    source_id=source.source_id,
                    error=str(e),
                )
                return {"new": 0, "error": str(e)}

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_run(self) -> datetime | None:
        return self._last_run

    def status(self) -> dict[str, object]:
        return {
            "running": self._running,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "tracked_sources": len([
                s for s in self._registry.fetchable()
                if s.source_type in _RSS_SOURCE_TYPES
            ]),
        }
