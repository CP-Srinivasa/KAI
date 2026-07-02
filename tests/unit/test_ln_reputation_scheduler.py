"""Tests for LnReputationScheduler: the tick records node-reputation telemetry
and never breaks the scheduler (fail-soft, read-only, no capital path)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.orchestrator.ln_reputation_scheduler import _JOB_ID, LnReputationScheduler


def test_start_registers_interval_job() -> None:
    sched = LnReputationScheduler(interval_seconds=900)
    with (
        patch.object(sched._scheduler, "add_job") as add_job,
        patch.object(sched._scheduler, "start"),
    ):
        sched.start()
    assert add_job.call_args.kwargs["id"] == _JOB_ID
    assert add_job.call_args.kwargs["seconds"] == 900


@pytest.mark.asyncio
async def test_tick_records_reputation() -> None:
    sched = LnReputationScheduler(interval_seconds=900)
    rec = AsyncMock(return_value=None)
    with patch("app.orchestrator.ln_reputation_scheduler.record_ln_reputation", rec):
        await sched._tick()
    rec.assert_awaited_once()


@pytest.mark.asyncio
async def test_tick_failure_is_swallowed() -> None:
    sched = LnReputationScheduler(interval_seconds=900)
    with patch(
        "app.orchestrator.ln_reputation_scheduler.record_ln_reputation",
        AsyncMock(side_effect=RuntimeError("lnd boom")),
    ):
        await sched._tick()  # must NOT raise
