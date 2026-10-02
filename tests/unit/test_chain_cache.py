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
    monkeypatch.setattr(chain_cache, "CHAIN_CACHE_TTL_SECONDS", -1.0)
    await chain_cache.get_cached_chain_status()
    await chain_cache._refresh_task
    assert calls == 2


# ---------------------------------------------------------------------------
# get_fresh_chain_status (Oracle UC-4, 02.10.2026): der Cache frischt sich nur auf
# Anfrage auf. Ohne Wartezeit bekam der erste Abruf nach >2 min Ruhe ein 503 "stale",
# bei einem bezahlten Abruf sogar als "bezahlt, nicht geliefert" gezaehlt.
# ---------------------------------------------------------------------------


async def test_fresh_waits_for_the_refresh_of_a_stale_cache(monkeypatch) -> None:
    _enable(monkeypatch)
    calls = 0

    async def _fetch() -> ChainStatus:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return _ok(blocks=100 + calls)

    monkeypatch.setattr(chain_cache, "get_chain_status", _fetch)
    status, _ = await chain_cache.get_fresh_chain_status(max_wait_s=2.0)
    assert status.blocks == 101, "kalter Cache: wartet auf den ersten Abruf statt 'pending'"
    chain_cache._cached_at -= chain_cache.CHAIN_CACHE_TTL_SECONDS * 3  # lange Ruhe
    status, age = await chain_cache.get_fresh_chain_status(max_wait_s=2.0)
    assert status.blocks == 102 and age is not None and age < 1.0


async def test_fresh_gives_up_after_max_wait_without_hanging(monkeypatch) -> None:
    _enable(monkeypatch)
    release = asyncio.Event()

    async def _slow() -> ChainStatus:
        await release.wait()  # bitcoind haengt (cs_main)
        return _ok(blocks=2)

    async def _first() -> ChainStatus:
        return _ok(blocks=1)

    monkeypatch.setattr(chain_cache, "get_chain_status", _first)
    await chain_cache.get_fresh_chain_status(max_wait_s=1.0)
    chain_cache._cached_at -= chain_cache.CHAIN_CACHE_TTL_SECONDS * 3
    monkeypatch.setattr(chain_cache, "get_chain_status", _slow)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    status, age = await chain_cache.get_fresh_chain_status(max_wait_s=0.2)
    assert loop.time() - t0 < 1.0, "die Wartezeit ist begrenzt"
    assert status.blocks == 1 and age is not None and age > chain_cache.CHAIN_CACHE_TTL_SECONDS
    # Die Auffrischung laeuft weiter (geschuetzt) und landet spaeter im Cache.
    release.set()
    await chain_cache._refresh_task
    status, _ = await chain_cache.get_cached_chain_status()
    assert status.blocks == 2


async def test_fresh_disabled_returns_immediately(monkeypatch) -> None:
    _enable(monkeypatch, enabled=False)
    status, age = await chain_cache.get_fresh_chain_status(max_wait_s=5.0)
    assert status.state == "disabled" and age is None
    assert chain_cache._refresh_task is None
