"""Tests for ChainFeeShadowScheduler: the tick captures the sovereign on-chain
fee truth and never breaks the scheduler (fail-soft, read-only, no capital)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.orchestrator.chain_fee_shadow_scheduler import _JOB_ID, ChainFeeShadowScheduler


def test_start_registers_interval_job() -> None:
    sched = ChainFeeShadowScheduler(interval_seconds=900)
    with (
        patch.object(sched._scheduler, "add_job") as add_job,
        patch.object(sched._scheduler, "start"),
    ):
        sched.start()
    assert add_job.call_args.kwargs["id"] == _JOB_ID
    assert add_job.call_args.kwargs["seconds"] == 900


@pytest.mark.asyncio
async def test_tick_records_fee_shadow() -> None:
    sched = ChainFeeShadowScheduler(interval_seconds=900)
    rec = AsyncMock(return_value=None)
    with patch("app.orchestrator.chain_fee_shadow_scheduler.record_onchain_fee_shadow", rec):
        await sched._tick()
    rec.assert_awaited_once()


@pytest.mark.asyncio
async def test_tick_failure_is_swallowed() -> None:
    sched = ChainFeeShadowScheduler(interval_seconds=900)
    with patch(
        "app.orchestrator.chain_fee_shadow_scheduler.record_onchain_fee_shadow",
        AsyncMock(side_effect=RuntimeError("rpc boom")),
    ):
        await sched._tick()  # must NOT raise
