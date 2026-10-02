"""Background-refreshed cache for KAI's sovereign-chain status (L1).

KAI's own bitcoind node on a constrained host (Raspberry Pi) can hold ``cs_main``
for minutes during slow chainstate flushes, making *any* authenticated RPC stall
for that long. A synchronous :func:`app.chain.adapter.get_chain_status` on the
request path would then make the dashboard block (or time out) for minutes. This
module decouples the two:

  * the dashboard reads :func:`get_cached_chain_status` — it NEVER blocks on
    bitcoind; it returns the last successfully fetched :class:`ChainStatus` (plus
    its age in seconds) instantly, or a ``pending`` status while the cache is
    still cold;
  * a single in-flight background refresh (fire-and-forget) repopulates the cache
    when it goes stale, tolerating the node's slow RPC via the adapter's own
    (generous) timeout.

Read-only, fail-closed, default-off — it inherits every guarantee of the adapter
it wraps and adds no new network surface of its own.
"""

from __future__ import annotations

import asyncio
import time

from app.chain.adapter import ChainStatus, get_chain_status
from app.core.settings import get_settings

# Serve a cached value up to this age (seconds) before kicking a background
# refresh. Refreshes are still single-flight, so a slow node cannot pile up
# concurrent RPCs even if requests arrive faster than this.
CHAIN_CACHE_TTL_SECONDS = 60.0

_cached: ChainStatus | None = None
_cached_at: float = 0.0
_refresh_task: asyncio.Task[None] | None = None


def _pending() -> ChainStatus:
    """Honest cold-cache state: enabled but no successful fetch yet."""
    return ChainStatus(state="pending", reachable=False, reason="chain status warming up")


async def _refresh() -> None:
    global _cached, _cached_at
    # get_chain_status never raises by contract; guard anyway so a stray error
    # cannot kill the background task and wedge the single-flight gate.
    try:
        status = await get_chain_status()
    except Exception:  # noqa: BLE001 — defensive; adapter already never raises
        return
    _cached = status
    _cached_at = time.monotonic()


def _start_refresh_if_idle() -> None:
    """Start a single background refresh; no-op if one is already in flight.

    Safe under a single event loop: there is no ``await`` between the in-flight
    check and ``create_task``, so two callers cannot both start a refresh.
    """
    global _refresh_task
    if _refresh_task is not None and not _refresh_task.done():
        return
    _refresh_task = asyncio.create_task(_refresh())


async def get_cached_chain_status() -> tuple[ChainStatus, float | None]:
    """Return ``(status, age_seconds)`` without ever blocking on bitcoind.

    ``age_seconds`` is the age of the cached snapshot, or ``None`` while the cache
    is cold. When the cache is older than the TTL (or cold) a single background
    refresh is kicked off; the current call still returns immediately with the
    stale/``pending`` value. Subsequent calls observe the refreshed value once it
    completes.
    """
    # Default-off short-circuit: a disabled feature reports ``disabled`` instantly
    # (no network, no background task, no misleading ``pending``).
    if not get_settings().chain.enabled:
        return ChainStatus.disabled(), None
    if _cached is None:
        _start_refresh_if_idle()
        return _pending(), None
    age = time.monotonic() - _cached_at
    if age > CHAIN_CACHE_TTL_SECONDS:
        _start_refresh_if_idle()
    return _cached, age


async def get_fresh_chain_status(max_wait_s: float) -> tuple[ChainStatus, float | None]:
    """Like :func:`get_cached_chain_status`, but waits up to ``max_wait_s`` for a refresh.

    For the paid Oracle path (UC-4) only. The cache refreshes lazily, on request: after
    a quiet spell the first caller saw the stale value and got 503 ``stale`` -- for a
    paying client that was counted as "paid, not delivered" (02.10.2026). Here a stale
    or cold cache waits, bounded, for the single-flight refresh it just started. The
    refresh is shielded: giving up does not cancel it, a later call still profits. The
    dashboard keeps the never-blocking variant (bitcoind can hold ``cs_main`` for
    minutes).
    """
    status, age = await get_cached_chain_status()
    if age is not None and age <= CHAIN_CACHE_TTL_SECONDS:
        return status, age
    task = _refresh_task
    if task is None or task.done():
        return status, age
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=max_wait_s)
    except TimeoutError:
        return status, age
    return await get_cached_chain_status()


def reset_cache_for_tests() -> None:
    """Clear module state (test seam only; not used in production paths)."""
    global _cached, _cached_at, _refresh_task
    _cached = None
    _cached_at = 0.0
    _refresh_task = None


__all__ = [
    "CHAIN_CACHE_TTL_SECONDS",
    "get_cached_chain_status",
    "get_fresh_chain_status",
    "reset_cache_for_tests",
]
