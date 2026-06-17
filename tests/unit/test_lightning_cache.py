"""Unit tests for the background-refreshed Lightning node-status cache.

Covers the request-path guarantees (default-off short-circuit, non-blocking cold
start, single-flight refresh, TTL gating) plus the anti-flicker merge that keeps
the last full ``getinfo`` snapshot over a degraded reachable-but-no-info poll.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.lightning import cache as ln_cache
from app.lightning.adapter import LightningNodeStatus


@pytest.fixture(autouse=True)
def _clean_cache():
    ln_cache.reset_cache_for_tests()
    yield
    ln_cache.reset_cache_for_tests()


def _enable(monkeypatch, enabled: bool = True) -> None:
    monkeypatch.setattr(
        ln_cache,
        "get_settings",
        lambda: SimpleNamespace(lightning=SimpleNamespace(enabled=enabled)),
    )


def _full(alias: str = "kai-node", peers: int = 3) -> LightningNodeStatus:
    """Richest snapshot: getinfo detail AND balances present (richness 2)."""
    return LightningNodeStatus(
        state="ok",
        reachable=True,
        server_state="SERVER_ACTIVE",
        info_available=True,
        num_peers=peers,
        alias=alias,
        identity_pubkey="024a7f9c",
        balances_available=True,
        wallet_confirmed_sat=1_949_654,
        wallet_total_sat=1_949_654,
        channel_local_sat=0,
        channel_remote_sat=0,
    )


def _reachable_no_info() -> LightningNodeStatus:
    """Liveness only — getinfo and balances both flaked (richness 0)."""
    return LightningNodeStatus(
        state="ok", reachable=True, server_state="SERVER_ACTIVE", info_available=False
    )


def _info_no_balances() -> LightningNodeStatus:
    """getinfo succeeded but the balance calls flaked (richness 1)."""
    return LightningNodeStatus(
        state="ok",
        reachable=True,
        server_state="SERVER_ACTIVE",
        info_available=True,
        num_peers=3,
        alias="kai-node",
        identity_pubkey="024a7f9c",
        balances_available=False,
    )


async def test_disabled_short_circuits_without_network(monkeypatch) -> None:
    _enable(monkeypatch, enabled=False)
    called = False

    async def _never() -> LightningNodeStatus:  # pragma: no cover - must not run
        nonlocal called
        called = True
        return _full()

    monkeypatch.setattr(ln_cache, "get_node_status", _never)
    status, age = await ln_cache.get_cached_node_status()
    assert status.state == "disabled" and age is None
    assert called is False
    assert ln_cache._refresh_task is None


async def test_cold_returns_pending_then_warms(monkeypatch) -> None:
    _enable(monkeypatch)

    async def _fetch() -> LightningNodeStatus:
        return _full(alias="kai-node", peers=2)

    monkeypatch.setattr(ln_cache, "get_node_status", _fetch)

    status, age = await ln_cache.get_cached_node_status()
    assert status.state == "pending" and age is None  # cold: never blocks

    await ln_cache._refresh_task

    status, age = await ln_cache.get_cached_node_status()
    assert status.info_available is True and status.alias == "kai-node"
    assert status.num_peers == 2 and age is not None


async def test_single_flight_under_concurrency(monkeypatch) -> None:
    _enable(monkeypatch)
    calls = 0

    async def _slow() -> LightningNodeStatus:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return _full()

    monkeypatch.setattr(ln_cache, "get_node_status", _slow)

    results = await asyncio.gather(*(ln_cache.get_cached_node_status() for _ in range(5)))
    assert all(r[0].state == "pending" for r in results)

    await ln_cache._refresh_task
    assert calls == 1


async def test_anti_flicker_keeps_last_full_snapshot(monkeypatch) -> None:
    """A reachable-but-no-getinfo poll must NOT blank out the last full snapshot."""
    _enable(monkeypatch)

    seq = [_full(alias="kai-node", peers=3), _reachable_no_info(), _reachable_no_info()]

    async def _fetch() -> LightningNodeStatus:
        return seq.pop(0)

    monkeypatch.setattr(ln_cache, "get_node_status", _fetch)

    # 1st refresh: full snapshot.
    await ln_cache.get_cached_node_status()
    await ln_cache._refresh_task
    status, age_full = await ln_cache.get_cached_node_status()
    assert status.info_available is True and status.alias == "kai-node"

    # 2nd refresh returns ok-without-info (getinfo flaked) -> retain full detail.
    monkeypatch.setattr(ln_cache, "_TTL_SECONDS", -1.0)  # force refresh on read
    await ln_cache.get_cached_node_status()
    await ln_cache._refresh_task
    status, age_after = await ln_cache.get_cached_node_status()
    assert status.info_available is True and status.alias == "kai-node"  # NOT blanked
    assert status.num_peers == 3
    assert age_after >= age_full  # snapshot age keeps growing honestly


async def test_genuine_unavailable_overwrites(monkeypatch) -> None:
    """A genuine unavailable poll DOES replace a stale full snapshot (honest down)."""
    _enable(monkeypatch)
    seq = [_full(), LightningNodeStatus.unavailable("node down")]

    async def _fetch() -> LightningNodeStatus:
        return seq.pop(0)

    monkeypatch.setattr(ln_cache, "get_node_status", _fetch)

    await ln_cache.get_cached_node_status()
    await ln_cache._refresh_task
    assert (await ln_cache.get_cached_node_status())[0].info_available is True

    monkeypatch.setattr(ln_cache, "_TTL_SECONDS", -1.0)
    await ln_cache.get_cached_node_status()
    await ln_cache._refresh_task
    status, _ = await ln_cache.get_cached_node_status()
    assert status.state == "unavailable" and status.reachable is False


async def test_anti_flicker_keeps_balances_on_balance_flake(monkeypatch) -> None:
    """getinfo ok but balances flaked must NOT blank the cached wallet/channel sats."""
    _enable(monkeypatch)
    seq = [_full(), _info_no_balances(), _info_no_balances()]

    async def _fetch() -> LightningNodeStatus:
        return seq.pop(0)

    monkeypatch.setattr(ln_cache, "get_node_status", _fetch)

    # Warm with the richest snapshot (info + balances).
    await ln_cache.get_cached_node_status()
    await ln_cache._refresh_task
    assert (await ln_cache.get_cached_node_status())[0].wallet_confirmed_sat == 1_949_654

    # Subsequent polls keep getinfo but lose balances -> retain the full snapshot.
    monkeypatch.setattr(ln_cache, "_TTL_SECONDS", -1.0)
    await ln_cache.get_cached_node_status()
    await ln_cache._refresh_task
    status, _ = await ln_cache.get_cached_node_status()
    assert status.balances_available is True  # NOT blanked
    assert status.wallet_confirmed_sat == 1_949_654
