"""Tests for PositionMonitorScheduler: job wiring, tick callback, fail-closed.

We don't time-advance the apscheduler — the scheduler's job correctness
is the library's contract.  We test the unit we own: that the job is
registered with the right interval & id, and that `_tick` calls
run_position_monitor_once and swallows exceptions so the next tick still
runs.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.orchestrator.position_monitor_scheduler import (
    _JOB_ID,
    PositionMonitorScheduler,
)


@pytest.mark.asyncio
async def test_start_registers_job_with_configured_interval() -> None:
    sched = PositionMonitorScheduler(interval_seconds=45, provider="coingecko")
    sched.start()
    try:
        job = sched._scheduler.get_job(_JOB_ID)
        assert job is not None
        # apscheduler stores the interval as a timedelta on the trigger
        assert job.trigger.interval.total_seconds() == 45
        assert job.max_instances == 1
    finally:
        sched.stop()


@pytest.mark.asyncio
async def test_start_registers_exactly_one_job() -> None:
    sched = PositionMonitorScheduler(interval_seconds=30)
    sched.start()
    try:
        jobs = sched._scheduler.get_jobs()
        assert len([j for j in jobs if j.id == _JOB_ID]) == 1
    finally:
        sched.stop()


@pytest.mark.asyncio
async def test_tick_calls_run_position_monitor_once() -> None:
    sched = PositionMonitorScheduler(interval_seconds=60, provider="coingecko")

    called_with: dict[str, object] = {}

    async def fake_run_once(*, provider: str | None = None) -> dict[str, object]:
        called_with["provider"] = provider
        return {"checked": 2, "no_market_data": 0, "triggered": 1, "closes": []}

    with patch(
        "app.orchestrator.trading_loop.run_position_monitor_once",
        side_effect=fake_run_once,
    ):
        await sched._tick()

    assert called_with == {"provider": "coingecko"}


@pytest.mark.asyncio
async def test_tick_swallows_exception_and_logs() -> None:
    """A failing monitor call must not propagate — the next scheduled tick
    must be able to run. Fail-closed: log, don't raise."""
    sched = PositionMonitorScheduler(interval_seconds=60)

    async def fake_run_once(**_: object) -> dict[str, object]:
        raise RuntimeError("market data boom")

    with patch(
        "app.orchestrator.trading_loop.run_position_monitor_once",
        side_effect=fake_run_once,
    ):
        # Must NOT raise.
        await sched._tick()
