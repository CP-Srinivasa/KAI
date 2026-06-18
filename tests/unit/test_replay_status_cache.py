"""Unit tests für den background-refreshed Replay-SSOT-Status-Cache (#314).

Deckt die Garantien, die ihn auf dem Dashboard-Request-Pfad sicher machen:
non-blocking Cold-Start mit Single-Flight-Hintergrund-Refresh, TTL-gated
Recompute und Error-Swallow (Single-Flight-Gate verklemmt nie).
"""

from __future__ import annotations

import asyncio

import pytest

from app.observability import replay_status_cache as cache
from app.observability.replay_status import ReplayStatus


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.reset_cache_for_tests()
    yield
    cache.reset_cache_for_tests()


def _status(positions: int = 3) -> ReplayStatus:
    return ReplayStatus(
        state="ok",
        available=True,
        positions=positions,
        fills_replayed=170,
        skipped_events=0,
        lifecycle_errors=0,
        reason="",
    )


async def test_cold_returns_none_then_warms(monkeypatch) -> None:
    monkeypatch.setattr(cache, "load_replay_status", lambda: _status())

    status, age = await cache.get_cached_replay_status()
    assert status is None and age is None  # cold: never blocks

    await cache._refresh_task

    status, age = await cache.get_cached_replay_status()
    assert status is not None and status.positions == 3
    assert age is not None and age >= 0


async def test_single_flight_under_concurrency(monkeypatch) -> None:
    calls = 0

    def _slow() -> ReplayStatus:
        nonlocal calls
        calls += 1
        return _status()

    monkeypatch.setattr(cache, "load_replay_status", _slow)

    results = await asyncio.gather(*(cache.get_cached_replay_status() for _ in range(5)))
    assert all(r[0] is None and r[1] is None for r in results)  # all non-blocking

    await cache._refresh_task
    assert calls == 1  # only ONE compute despite 5 concurrent cold readers


async def test_ttl_gates_recompute(monkeypatch) -> None:
    calls = 0

    def _compute() -> ReplayStatus:
        nonlocal calls
        calls += 1
        return _status(positions=calls)

    monkeypatch.setattr(cache, "load_replay_status", _compute)

    await cache.get_cached_replay_status()  # cold -> kicks compute #1
    await cache._refresh_task
    assert calls == 1

    status, age = await cache.get_cached_replay_status()  # within TTL -> cached
    assert calls == 1 and age is not None and status is not None and status.positions == 1

    monkeypatch.setattr(cache, "_TTL_SECONDS", -1.0)  # force stale
    await cache.get_cached_replay_status()
    await cache._refresh_task
    assert calls == 2


async def test_refresh_swallows_compute_error(monkeypatch) -> None:
    def _boom() -> ReplayStatus:
        raise RuntimeError("replay unreadable")

    monkeypatch.setattr(cache, "load_replay_status", _boom)

    status, age = await cache.get_cached_replay_status()
    assert status is None and age is None

    await cache._refresh_task  # must not raise
    status, age = await cache.get_cached_replay_status()
    assert status is None and age is None  # stays cold, no fabricated data
