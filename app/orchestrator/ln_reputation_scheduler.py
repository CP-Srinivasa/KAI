"""Periodic capture of KAI's Lightning node reputation telemetry (read-only).

Runs :func:`app.lightning.reputation.record_ln_reputation` on a fixed interval so
an uptime / connectivity / routing-income trend from KAI's OWN lnd node
accumulates over time — the raw material for the OTS-anchored node track-record
inside the Truth Oracle. Strictly read-only, no capital path, and fail-soft: a
tick error never breaks the scheduler. No-op when ``lightning.enabled`` is False
(the recorder itself short-circuits on a ``disabled`` node status).
"""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.logging import get_logger
from app.lightning.reputation import record_ln_reputation

logger = get_logger(__name__)

_JOB_ID = "ln_reputation"


class LnReputationScheduler:
    """Schedules periodic Lightning node-reputation capture (read-only)."""

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
        logger.info("ln_reputation_scheduler_started", interval_seconds=self._interval_seconds)

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("ln_reputation_scheduler_stopped")

    async def _tick(self) -> None:
        try:
            rec = await record_ln_reputation()
        except Exception as exc:  # noqa: BLE001 — never break the scheduler tick
            logger.error("ln_reputation_tick_failed", error=str(exc))
            return
        if rec is not None:
            logger.info(
                "ln_reputation_recorded",
                state=rec.state,
                reachable=rec.reachable,
                num_peers=rec.num_peers,
                num_active_channels=rec.num_active_channels,
            )
