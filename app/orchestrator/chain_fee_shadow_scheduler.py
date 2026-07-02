"""Periodic capture of KAI's sovereign on-chain fee truth (L1 shadow).

Runs ``app.chain.fee_shadow.record_onchain_fee_shadow`` on a fixed interval so a
historical fee/mempool series from KAI's OWN bitcoind node accumulates for the
future on-chain/Lightning settlement-cost layer (Phase 4/5). Strictly read-only,
DECOUPLED from the trading CostModel (an on-chain sat/vB fee must never fold into
exchange-trade costs — see fee_shadow.py), and fail-soft: a tick error never
breaks the scheduler. No capital path. No-op when ``chain.enabled`` is False
(the recorder itself short-circuits on a non-``ok`` chain status).
"""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.chain.fee_shadow import record_onchain_fee_shadow
from app.core.logging import get_logger

logger = get_logger(__name__)

_JOB_ID = "chain_fee_shadow"


class ChainFeeShadowScheduler:
    """Schedules periodic sovereign on-chain fee-truth capture (L1, read-only)."""

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
        logger.info("chain_fee_shadow_scheduler_started", interval_seconds=self._interval_seconds)

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("chain_fee_shadow_scheduler_stopped")

    async def _tick(self) -> None:
        try:
            rec = await record_onchain_fee_shadow()
        except Exception as exc:  # noqa: BLE001 — never break the scheduler tick
            logger.error("chain_fee_shadow_tick_failed", error=str(exc))
            return
        if rec is not None:
            logger.info(
                "chain_fee_shadow_recorded",
                fee_sat_vb=rec.fee_sat_vb,
                mempool_tx=rec.mempool_tx,
                blocks=rec.blocks,
            )
