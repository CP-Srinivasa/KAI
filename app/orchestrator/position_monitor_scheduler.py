"""Periodic paper-position stop/take-profit monitor.

The trading loop opens positions in response to new analyses, but never
revisits them — so `check_stop_take()` is only ever invoked from the CLI
command `trading paper-monitor-positions`.  That left realized_pnl at 0.0
for every filled paper order (12+ fills, 0 closes) and made the Phase 5
re-entry gate measurable only on paper-trivial terms.

This scheduler fills the gap: every `interval_seconds` it rehydrates the
paper engine from the audit log, fetches a fresh price for each open
symbol, and closes any position whose SL/TP level fired.  Missing/stale
prices skip the symbol (never force-close on bad data).

Mirrors the RSSScheduler shape (same APScheduler, same start/stop, same
`max_instances=1` guard so slow monitor cycles don't stack up).
"""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.logging import get_logger

logger = get_logger(__name__)

_JOB_ID = "position_monitor"


class PositionMonitorScheduler:
    """Schedules periodic SL/TP checks for all open paper positions."""

    def __init__(
        self,
        *,
        interval_seconds: int,
        provider: str | None = None,
    ) -> None:
        self._interval_seconds = interval_seconds
        self._provider = provider
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
        logger.info(
            "position_monitor_scheduler_started",
            interval_seconds=self._interval_seconds,
            provider=self._provider or "default",
        )

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("position_monitor_scheduler_stopped")

    async def _tick(self) -> None:
        # Local import: trading_loop pulls the execution stack, we don't want
        # to drag that into module import time (FastAPI lifespan starts fast).
        from app.orchestrator.trading_loop import run_position_monitor_once

        try:
            summary = await run_position_monitor_once(provider=self._provider)
        except Exception as exc:  # noqa: BLE001
            # Fail-closed: log, never propagate — the next tick must still run.
            logger.error("position_monitor_tick_failed", error=str(exc))
            return

        logger.info(
            "position_monitor_tick_complete",
            checked=summary.get("checked"),
            no_market_data=summary.get("no_market_data"),
            triggered=summary.get("triggered"),
        )
