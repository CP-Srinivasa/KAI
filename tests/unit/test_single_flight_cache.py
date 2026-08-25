from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.api.routers.dashboard_quality_cache import SingleFlightCache


class FakeClock:
    def __init__(self, start: float = 100.0) -> None:
        self.value = start
        self.base_utc = datetime(2026, 8, 25, 12, 0, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.value

    def now_utc(self) -> datetime:
        return self.base_utc + timedelta(seconds=self.value)

    def advance(self, seconds: float) -> None:
        self.value += seconds


@pytest.mark.asyncio
async def test_single_flight_cache_serves_fresh_until_ttl_then_refreshes() -> None:
    clock = FakeClock()
    calls = 0

    async def refresh() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        clock.advance(0.123)
        return {"value": calls}

    cache = SingleFlightCache(
        refresh=refresh, ttl_s=10.0, clock=clock.monotonic, now_utc=clock.now_utc
    )

    first = await cache.get()
    clock.advance(5.0)
    second = await cache.get()
    clock.advance(6.0)
    third = await cache.get()

    assert first["value"] == 1
    assert first["cache"]["stale"] is False
    assert first["cache"]["compute_ms"] == 123.0
    assert second["value"] == 1
    assert second["cache"]["age_s"] == 5.0
    assert third["value"] == 2
    assert calls == 2


@pytest.mark.asyncio
async def test_single_flight_cache_shares_empty_cache_refresh() -> None:
    calls = 0

    async def refresh() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return {"marker": "shared"}

    cache = SingleFlightCache(refresh=refresh, ttl_s=10.0)

    responses = await asyncio.gather(*(cache.get() for _ in range(5)))

    assert calls == 1
    assert {response["marker"] for response in responses} == {"shared"}
    assert all(response["cache"]["stale"] is False for response in responses)


@pytest.mark.asyncio
async def test_single_flight_cache_serves_stale_while_revalidate() -> None:
    clock = FakeClock()
    calls = 0
    refresh_started = asyncio.Event()
    release_refresh = asyncio.Event()

    async def refresh() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"marker": "old"}
        refresh_started.set()
        await release_refresh.wait()
        return {"marker": "new"}

    cache = SingleFlightCache(
        refresh=refresh, ttl_s=10.0, clock=clock.monotonic, now_utc=clock.now_utc
    )

    seeded = await cache.get()
    assert seeded["marker"] == "old"
    clock.advance(11.0)

    refresh_task = asyncio.create_task(cache.get())
    await refresh_started.wait()
    stale = await cache.get()
    release_refresh.set()
    fresh = await refresh_task
    after_refresh = await cache.get()

    assert stale["marker"] == "old"
    assert stale["cache"]["stale"] is True
    assert stale["cache"]["age_s"] == 11.0
    assert fresh["marker"] == "new"
    assert fresh["cache"]["stale"] is False
    assert after_refresh["marker"] == "new"
    assert after_refresh["cache"]["stale"] is False
    assert calls == 2


@pytest.mark.asyncio
async def test_single_flight_cache_refresh_error_propagates_and_keeps_old_cache() -> None:
    clock = FakeClock()
    mode = "old"
    refresh_started = asyncio.Event()
    release_refresh = asyncio.Event()

    async def refresh() -> dict[str, Any]:
        if mode == "old":
            return {"marker": "old"}
        refresh_started.set()
        await release_refresh.wait()
        if mode == "broken":
            raise RuntimeError("refresh failed")
        return {"marker": "new"}

    cache = SingleFlightCache(
        refresh=refresh, ttl_s=10.0, clock=clock.monotonic, now_utc=clock.now_utc
    )

    seeded = await cache.get()
    assert seeded["marker"] == "old"
    clock.advance(11.0)

    mode = "broken"
    broken_task = asyncio.create_task(cache.get())
    await refresh_started.wait()
    stale_during_failure = await cache.get()
    release_refresh.set()
    with pytest.raises(RuntimeError, match="refresh failed"):
        await broken_task

    assert stale_during_failure["marker"] == "old"
    assert stale_during_failure["cache"]["stale"] is True

    refresh_started = asyncio.Event()
    release_refresh = asyncio.Event()
    mode = "new"
    retry_task = asyncio.create_task(cache.get())
    await refresh_started.wait()
    stale_after_failure = await cache.get()
    release_refresh.set()
    retry = await retry_task

    assert stale_after_failure["marker"] == "old"
    assert stale_after_failure["cache"]["stale"] is True
    assert retry["marker"] == "new"
    assert retry["cache"]["stale"] is False


def test_single_flight_cache_rebinds_lock_and_task_for_new_event_loop() -> None:
    clock = FakeClock()
    calls = 0
    cache: SingleFlightCache

    async def refresh() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"calls": calls}

    cache = SingleFlightCache(
        refresh=refresh, ttl_s=10.0, clock=clock.monotonic, now_utc=clock.now_utc
    )

    async def run_once() -> tuple[dict[str, Any], asyncio.AbstractEventLoop | None]:
        payload = await cache.get()
        return payload, cache._lock_loop

    first_payload, first_loop = asyncio.run(run_once())
    clock.advance(11.0)
    second_payload, second_loop = asyncio.run(run_once())

    assert first_payload["calls"] == 1
    assert second_payload["calls"] == 2
    assert first_loop is not None
    assert second_loop is not None
    assert first_loop is not second_loop
    assert cache._compute_task is None
    assert cache._compute_task_loop is None
