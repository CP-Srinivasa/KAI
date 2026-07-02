"""DuckDB Compaction Worker Scheduler.

Periodically reads JSONL files and compacts them into DuckDB tables
for efficient analytical querying.
"""

from __future__ import annotations

import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.logging import get_logger
from app.storage.analytics_db import run_compaction

logger = get_logger(__name__)


class CompactionWorkerScheduler:
    """Schedules periodic DuckDB compaction from JSONL files."""

    def __init__(self, interval_minutes: int = 1) -> None:
        self._interval_minutes = interval_minutes
        self._scheduler = AsyncIOScheduler()

    def start(self) -> None:
        """Start the compaction scheduler."""
        self._scheduler.add_job(
            self._run_job,
            trigger="interval",
            minutes=self._interval_minutes,
            id="duckdb_compaction",
            replace_existing=True,
            max_instances=1,
        )
        self._scheduler.start()

        # Run once immediately on startup asynchronously
        asyncio.create_task(self._run_job())

        logger.info(
            "duckdb_compaction_scheduler_started",
            interval_minutes=self._interval_minutes,
        )

    def stop(self) -> None:
        """Stop the compaction scheduler."""
        self._scheduler.shutdown(wait=False)
        logger.info("duckdb_compaction_scheduler_stopped")

    async def _run_job(self) -> None:
        """Run the compaction job."""
        try:
            await asyncio.to_thread(run_compaction)
        except Exception as exc:  # noqa: BLE001
            logger.error("DuckDB compaction job failed: %s", exc)
