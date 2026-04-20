"""Periodic TradingView -> AlertAudit bridge scheduler (D-156c).

Runs ``persist_tv_events_as_alert_audits`` on a fixed interval so newly
arrived TV-webhook events land in ``alert_audit.jsonl`` without operator
intervention.  The auto-annotator picks them up on its own schedule
(``alerts auto-annotate``), closing the TV-4 Quality-Bar loop:

    TV webhook -> pending_signals -> [this scheduler] -> alert_audit
                                                        -> auto-annotator
                                                        -> tv4-quality-bar

Mirrors :class:`PositionMonitorScheduler` (APScheduler, ``max_instances=1``,
fail-closed ticks).  The bridge is idempotent, so a failed tick just defers
work to the next one.

Smoke events are filtered by default (same heuristic as
``provenance_metrics._summarize_tv_pipeline``) to keep the TV precision
bucket free of test-payload noise.
"""

from __future__ import annotations

from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.logging import get_logger

logger = get_logger(__name__)

_JOB_ID = "tv_bridge"


class TVBridgeScheduler:
    """Schedules periodic TV-event -> alert_audit bridging."""

    def __init__(
        self,
        *,
        interval_seconds: int,
        artifacts_dir: str | Path = "artifacts",
        include_smoke: bool = False,
    ) -> None:
        self._interval_seconds = interval_seconds
        self._artifacts_dir = Path(artifacts_dir)
        self._include_smoke = include_smoke
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
            "tv_bridge_scheduler_started",
            interval_seconds=self._interval_seconds,
            artifacts_dir=str(self._artifacts_dir),
            include_smoke=self._include_smoke,
        )

    def stop(self) -> None:
        # NEO-F-005: wait=False is intentional — an in-flight tick may log a
        # CancelledError, which is harmless because the bridge is idempotent
        # (doc_id dedup). A blocking wait=True could stall FastAPI shutdown
        # behind a multi-second JSONL scan.
        self._scheduler.shutdown(wait=False)
        logger.info("tv_bridge_scheduler_stopped")

    async def _tick(self) -> None:
        from app.alerts.tv_bridge import persist_tv_events_as_alert_audits

        try:
            counts = persist_tv_events_as_alert_audits(
                tv_pending_path=self._artifacts_dir / "tradingview_pending_signals.jsonl",
                alert_audit_path=self._artifacts_dir / "alert_audit.jsonl",
                include_smoke=self._include_smoke,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("tv_bridge_tick_failed", error=str(exc))
            return

        if counts.get("written", 0) > 0:
            logger.info("tv_bridge_tick_complete", **counts)
        else:
            logger.debug("tv_bridge_tick_complete", **counts)
