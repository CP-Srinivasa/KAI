"""Unit tests for the background-refreshed chain-status cache (L1).

Covers the three guarantees that make the cache safe to read on the request
path: default-off short-circuit, non-blocking cold start with single-flight
background refresh, and TTL-gated re-fetch.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.chain import cache as chain_cache
from app.chain.adapter import ChainStatus


@pytest.fixture(autouse=True)
def _clean_cache():
    chain_cache.reset_cache_for_tests()
    yield
    chain_cache.reset_cache_for_tests()


def _enable(monkeypatch, enabled: bool = True) -> None:
    monkeypatch.setattr(
        chain_cache,
        "get_settings",
        lambda: SimpleNamespace(chain=SimpleNamespace(enabled=enabled)),
    )


def _ok(blocks: int = 953902) -> ChainStatus:
    return ChainStatus(state="ok", reachable=True, chain="main", blocks=blocks, synced=True)


async def test_disabled_short_circuits_without_network(monkeypatch) -> None:
    _enable(monkeypatch, enabled=False)
    called = False

    async def _never() -> ChainStatus:  # pragma: no cover - must not run
        nonlocal called
        called = True
        return _ok()

    monkeypatch.setattr(chain_cache, "get_chain_status", _never)
    status, age = await chain_cache.get_cached_chain_status()
    assert status.state == "disabled" and age is None
    assert called is False
    assert chain_cache._refresh_task is None  # no background work started


async def test_cold_returns_pending_then_warms(monkeypatch) -> None:
    _enable(monkeypatch)

    async def _fetch() -> ChainStatus:
        return _ok()

    monkeypatch.setattr(chain_cache, "get_chain_status", _fetch)

    status, age = await chain_cache.get_cached_chain_status()
    assert status.state == "pending" and age is None  # cold: never blocks

    await chain_cache._refresh_task  # let the in-flight refresh complete

    status, age = await chain_cache.get_cached_chain_status()
    assert status.state == "ok" and status.blocks == 953902
    assert age is not None and age >= 0


async def test_single_flight_under_concurrency(monkeypatch) -> None:
    _enable(monkeypatch)
    calls = 0

    async def _slow() -> ChainStatus:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return _ok()

    monkeypatch.setattr(chain_cache, "get_chain_status", _slow)

    results = await asyncio.gather(*(chain_cache.get_cached_chain_status() for _ in range(5)))
    assert all(r[0].state == "pending" for r in results)  # all non-blocking

    await chain_cache._refresh_task
    assert calls == 1  # only ONE refresh despite 5 concurrent readers


async def test_ttl_gates_refetch(monkeypatch) -> None:
    _enable(monkeypatch)
    calls = 0

    async def _fetch() -> ChainStatus:
        nonlocal calls
        calls += 1
        return _ok(blocks=900000 + calls)

    monkeypatch.setattr(chain_cache, "get_chain_status", _fetch)

    await chain_cache.get_cached_chain_status()  # cold -> kicks refresh #1
    await chain_cache._refresh_task
    assert calls == 1

    # Fresh snapshot, within TTL -> served from cache, no new fetch.
    status, age = await chain_cache.get_cached_chain_status()
    assert status.state == "ok" and calls == 1 and age is not None

    # Past the TTL -> a new background refresh is triggered.
    monkeypatch.setattr(chain_cache, "_TTL_SECONDS", -1.0)
    await chain_cache.get_cached_chain_status()
    await chain_cache._refresh_task
    assert calls == 2
