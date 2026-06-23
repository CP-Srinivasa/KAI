"""Tests for TechnicalPaperScheduler: the tick drives the LONG-only feeder and
never breaks the scheduler (fail-soft, PAPER-only, doubly gated)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.orchestrator.technical_paper_scheduler import (
    _JOB_ID,
    TechnicalPaperScheduler,
    _loggable,
)


def test_start_registers_interval_job() -> None:
    sched = TechnicalPaperScheduler(interval_seconds=300)
    with (
        patch.object(sched._scheduler, "add_job") as add_job,
        patch.object(sched._scheduler, "start"),
    ):
        sched.start()
    assert add_job.call_args.kwargs["id"] == _JOB_ID
    assert add_job.call_args.kwargs["seconds"] == 300
    assert add_job.call_args.kwargs["max_instances"] == 1
    assert add_job.call_args.kwargs["coalesce"] is True


@pytest.mark.asyncio
async def test_tick_runs_feeder() -> None:
    sched = TechnicalPaperScheduler(interval_seconds=300)
    feeder = AsyncMock(return_value={"enabled": True, "processed": 3, "fed": 1})
    with patch("app.orchestrator.technical_paper_scheduler.run_feeder", feeder):
        await sched._tick()
    feeder.assert_awaited_once()


@pytest.mark.asyncio
async def test_tick_failure_is_swallowed() -> None:
    sched = TechnicalPaperScheduler(interval_seconds=300)
    with patch(
        "app.orchestrator.technical_paper_scheduler.run_feeder",
        AsyncMock(side_effect=RuntimeError("feeder boom")),
    ):
        await sched._tick()  # must NOT raise


def test_loggable_keeps_only_scalars() -> None:
    out = _loggable({"processed": 5, "fed": 1, "rows": [1, 2, 3], "note": "ok"})
    assert out == {"processed": 5, "fed": 1, "note": "ok"}
    assert _loggable("disabled") == {"result": "disabled"}
