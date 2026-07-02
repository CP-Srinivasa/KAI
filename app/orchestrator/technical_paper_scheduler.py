"""Periodic driver for the LONG-only technical-paper feeder (P0 automation link).

The ``technical_paper`` feeder (``app.observability.technical_paper_feeder``) was
merged CLI-only — nothing ran it on a schedule, so technical candidates never
flowed to PAPER automatically. This in-process APScheduler tick closes that gap:
it calls ``run_feeder()`` on a fixed interval so candidates accumulate as paper
fills for evidence.

Doubly gated + fail-soft: the scheduler only starts when
``technical_paper.scheduler_enabled`` is True, and ``run_feeder`` itself
short-circuits unless ``technical_paper.enabled`` is True. All feeder filters
(LONG-only, ``min_strength``, freshness, route-limits) stay in force. A tick
error never breaks the scheduler. No capital path (PAPER only).
"""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.logging import get_logger
from app.observability.technical_paper_feeder import run_feeder

logger = get_logger(__name__)

_JOB_ID = "technical_paper_feed"


class TechnicalPaperScheduler:
    """Schedules periodic LONG-only technical-paper feeding (PAPER, read-only intent)."""

    def __init__(self, *, interval_seconds: int) -> None:
        self._interval_seconds = interval_seconds
        self._scheduler = AsyncIOScheduler()

    def start(self) -> None:
        self._scheduler.add_job(
            self._tick,
            trigger="interval",
            seconds=self._interval_seconds,
            id=_JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self._scheduler.start()
        logger.info("technical_paper_scheduler_started", interval_seconds=self._interval_seconds)

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("technical_paper_scheduler_stopped")

    async def _tick(self) -> None:
        try:
            stats = await run_feeder()
        except Exception as exc:  # noqa: BLE001 — never break the scheduler tick
            logger.error("technical_paper_tick_failed", error=str(exc))
            return
        logger.info("technical_paper_tick", **_loggable(stats))


def _loggable(stats: object) -> dict[str, object]:
    """Keep only flat scalar stats for structured logging (defensive)."""
    if not isinstance(stats, dict):
        return {"result": str(stats)}
    return {k: v for k, v in stats.items() if isinstance(v, (str, int, float, bool))}
