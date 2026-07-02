"""Tests for TVBridgeScheduler: job wiring, tick offload, fail-closed, shutdown.

AUDIT-A1 regression guard: the tick must run the synchronous JSONL bridge work
OFF the event loop (asyncio.to_thread) so a growing audit log cannot wedge the
FastAPI loop (the #104 RSS-extraction wedge class). We test the unit we own:
job registration, that _tick delegates to persist_tv_events_as_alert_audits and
swallows exceptions, and that stop() does not block.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.orchestrator.tv_bridge_scheduler import _JOB_ID, TVBridgeScheduler


@pytest.mark.asyncio
async def test_start_registers_job_with_configured_interval() -> None:
    sched = TVBridgeScheduler(interval_seconds=300, artifacts_dir="artifacts")
    sched.start()
    try:
        job = sched._scheduler.get_job(_JOB_ID)
        assert job is not None
        assert job.trigger.interval.total_seconds() == 300
        assert job.max_instances == 1
        assert job.coalesce is True
    finally:
        sched.stop()


@pytest.mark.asyncio
async def test_tick_delegates_to_persist_with_configured_args(tmp_path: Path) -> None:
    sched = TVBridgeScheduler(
        interval_seconds=300, artifacts_dir=tmp_path, include_smoke=False, hmac_secret="s3cr3t"
    )
    captured: dict[str, object] = {}

    def fake_persist(**kwargs: object) -> dict[str, int]:
        captured.update(kwargs)
        return {"written": 2}

    with patch("app.alerts.tv_bridge.persist_tv_events_as_alert_audits", side_effect=fake_persist):
        await sched._tick()

    assert captured["tv_pending_path"] == tmp_path / "tradingview_pending_signals.jsonl"
    assert captured["alert_audit_path"] == tmp_path / "alert_audit.jsonl"
    assert captured["include_smoke"] is False
    assert captured["hmac_secret"] == "s3cr3t"


@pytest.mark.asyncio
async def test_tick_offloads_to_worker_thread(tmp_path: Path) -> None:
    """The synchronous bridge work must run in a worker thread, not the loop
    thread — otherwise a large JSONL scan wedges the FastAPI event loop."""
    import threading

    sched = TVBridgeScheduler(interval_seconds=300, artifacts_dir=tmp_path)
    loop_thread = threading.get_ident()
    seen: dict[str, int] = {}

    def fake_persist(**_: object) -> dict[str, int]:
        seen["thread"] = threading.get_ident()
        return {"written": 0}

    with patch("app.alerts.tv_bridge.persist_tv_events_as_alert_audits", side_effect=fake_persist):
        await sched._tick()

    assert seen["thread"] != loop_thread  # ran off the event-loop thread


@pytest.mark.asyncio
async def test_tick_swallows_exception_and_logs(tmp_path: Path) -> None:
    """A failing bridge call must not propagate — fail-closed, next tick runs."""
    sched = TVBridgeScheduler(interval_seconds=300, artifacts_dir=tmp_path)

    def boom(**_: object) -> dict[str, int]:
        raise RuntimeError("jsonl boom")

    with patch("app.alerts.tv_bridge.persist_tv_events_as_alert_audits", side_effect=boom):
        await sched._tick()  # must NOT raise


@pytest.mark.asyncio
async def test_tick_runs_tv_paper_feed(tmp_path: Path) -> None:
    """Each tick also emits PAPER envelopes for fresh TV alerts (gated feeder)."""
    sched = TVBridgeScheduler(interval_seconds=300, artifacts_dir=tmp_path)
    feed = AsyncMock(return_value={"enabled": True, "emitted": 1})
    with (
        patch(
            "app.alerts.tv_bridge.persist_tv_events_as_alert_audits",
            side_effect=lambda **_: {"written": 0},
        ),
        patch("app.observability.tradingview_paper_feeder.run_from_settings", feed),
    ):
        await sched._tick()
    feed.assert_awaited_once()


@pytest.mark.asyncio
async def test_tick_tv_paper_feed_failure_is_swallowed(tmp_path: Path) -> None:
    """A feeder error must not break the scheduler tick (fail-soft)."""
    sched = TVBridgeScheduler(interval_seconds=300, artifacts_dir=tmp_path)
    with (
        patch(
            "app.alerts.tv_bridge.persist_tv_events_as_alert_audits",
            side_effect=lambda **_: {"written": 0},
        ),
        patch(
            "app.observability.tradingview_paper_feeder.run_from_settings",
            AsyncMock(side_effect=RuntimeError("feed boom")),
        ),
    ):
        await sched._tick()  # must NOT raise


@pytest.mark.asyncio
async def test_stop_does_not_block() -> None:
    """Shutdown uses wait=False so an in-flight tick cannot stall FastAPI
    shutdown behind a multi-second JSONL scan (NEO-F-005)."""
    sched = TVBridgeScheduler(interval_seconds=300)
    sched.start()
    sched.stop()  # returns promptly; no hang (wait=False)
    sched.stop()  # idempotent — a second shutdown must not raise either
