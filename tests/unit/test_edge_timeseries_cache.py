"""Unit tests for the background-refreshed edge-timeseries cache (#319).

Covers the guarantees that make it safe on the dashboard request path: a
non-blocking cold start with single-flight background refresh, TTL-gated
re-compute, and that the heavy ledger read is delegated off the request path.
"""

from __future__ import annotations

import asyncio

import pytest

from app.observability import edge_timeseries_cache as cache
from app.observability.edge_timeseries import EdgeWindow


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.reset_cache_for_tests()
    yield
    cache.reset_cache_for_tests()


def _window(resolved: int = 12) -> EdgeWindow:
    return EdgeWindow(
        window_start="2026-06-01T00:00:00+00:00",
        window_end="2026-06-08T00:00:00+00:00",
        resolved=resolved,
        precision_pct=60.0,
        brier=0.24,
        ic_1h=0.1,
    )


async def test_cold_returns_empty_then_warms(monkeypatch) -> None:
    monkeypatch.setattr(cache, "load_edge_timeseries", lambda: [_window()])

    series, age = await cache.get_cached_edge_timeseries()
    assert series == [] and age is None  # cold: never blocks, honest empty

    await cache._refresh_task  # let the in-flight refresh complete

    series, age = await cache.get_cached_edge_timeseries()
    assert len(series) == 1 and series[0].resolved == 12
    assert age is not None and age >= 0


async def test_single_flight_under_concurrency(monkeypatch) -> None:
    calls = 0

    def _slow() -> list[EdgeWindow]:
        nonlocal calls
        calls += 1
        return [_window()]

    monkeypatch.setattr(cache, "load_edge_timeseries", _slow)

    results = await asyncio.gather(
        *(cache.get_cached_edge_timeseries() for _ in range(5))
    )
    assert all(r[0] == [] and r[1] is None for r in results)  # all non-blocking

    await cache._refresh_task
    assert calls == 1  # only ONE compute despite 5 concurrent cold readers


async def test_ttl_gates_recompute(monkeypatch) -> None:
    calls = 0

    def _compute() -> list[EdgeWindow]:
        nonlocal calls
        calls += 1
        return [_window(resolved=calls)]

    monkeypatch.setattr(cache, "load_edge_timeseries", _compute)

    await cache.get_cached_edge_timeseries()  # cold -> kicks compute #1
    await cache._refresh_task
    assert calls == 1

    # Fresh snapshot within TTL -> served from cache, no recompute.
    series, age = await cache.get_cached_edge_timeseries()
    assert calls == 1 and age is not None and series[0].resolved == 1

    # Past the TTL -> a new background recompute is triggered.
    monkeypatch.setattr(cache, "_TTL_SECONDS", -1.0)
    await cache.get_cached_edge_timeseries()
    await cache._refresh_task
    assert calls == 2


async def test_refresh_swallows_compute_error(monkeypatch) -> None:
    def _boom() -> list[EdgeWindow]:
        raise RuntimeError("ledger unreadable")

    monkeypatch.setattr(cache, "load_edge_timeseries", _boom)

    series, age = await cache.get_cached_edge_timeseries()
    assert series == [] and age is None

    await cache._refresh_task  # must not raise — error is swallowed
    # Cache stays cold (no fabricated data); next read re-kicks a refresh.
    series, age = await cache.get_cached_edge_timeseries()
    assert series == [] and age is None
