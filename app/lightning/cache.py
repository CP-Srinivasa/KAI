"""Background-refreshed cache for the Lightning node status (Phase 1).

lnd's ``getinfo`` over Tor is slow/intermittent: a fresh poll often passes the
cheap ``/v1/state`` liveness probe but times out on ``getinfo``, yielding an
``ok`` status with ``info_available=False`` and empty detail fields. A synchronous
per-request call therefore makes the Node panel flicker between populated and
empty. This module decouples the request path from that latency:

  * the dashboard reads :func:`get_cached_node_status` — it NEVER blocks on lnd
    and returns the last *richest* :class:`LightningNodeStatus` (plus its age);
  * a single in-flight background refresh repopulates the cache, and an
    anti-flicker merge keeps the last full (``info_available``) snapshot rather
    than overwriting it with a degraded getinfo-flake.

Read-only, fail-closed, default-off — inherits every guarantee of the adapter it
wraps and adds no new network surface of its own.
"""

from __future__ import annotations

import asyncio
import time

from app.core.settings import get_settings
from app.lightning.adapter import LightningNodeStatus, get_node_status

# Serve a cached value up to this age (seconds) before kicking a background
# refresh. Single-flight, so a slow node cannot pile up concurrent getinfo calls.
_TTL_SECONDS = 30.0

_cached: LightningNodeStatus | None = None
_cached_at: float = 0.0
_refresh_task: asyncio.Task[None] | None = None


def _pending() -> LightningNodeStatus:
    """Honest cold-cache state: enabled but no successful fetch yet."""
    return LightningNodeStatus(state="pending", reachable=False, reason="node status warming up")


def _richness(s: LightningNodeStatus) -> int:
    """Detail richness of a snapshot: getinfo fields + balance fields."""
    return (1 if s.info_available else 0) + (1 if s.balances_available else 0)


def _merge(fresh: LightningNodeStatus, cached: LightningNodeStatus | None) -> LightningNodeStatus:
    """Anti-flicker: keep the richest recent snapshot over a degraded poll.

    lnd's ``getinfo`` (Tor) AND the balance calls can each intermittently time
    out. A fresh poll wins when it reports a genuine non-``ok`` state
    (``unavailable``/``disabled`` — the node is really down/off) or when it is at
    least as detail-rich as what we already hold (getinfo fields + balances). But
    a reachable poll that lost detail (getinfo *or* balances flaked) does NOT
    overwrite a richer cached snapshot, so the panel keeps its identity/peers/
    channels AND its wallet/channel balances instead of blanking out.
    """
    if cached is None:
        return fresh
    if fresh.state in ("unavailable", "disabled"):
        return fresh
    if _richness(fresh) >= _richness(cached):
        return fresh
    return cached


async def _refresh() -> None:
    global _cached, _cached_at
    # get_node_status never raises by contract; guard anyway so a stray error
    # cannot kill the background task and wedge the single-flight gate.
    try:
        fresh = await get_node_status()
    except Exception:  # noqa: BLE001 — defensive; adapter already never raises
        return
    merged = _merge(fresh, _cached)
    # Only advance the snapshot timestamp when we actually adopt the fresh value.
    # Retaining the prior full snapshot lets its age grow honestly.
    if merged is not _cached:
        _cached = merged
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


async def get_cached_node_status() -> tuple[LightningNodeStatus, float | None]:
    """Return ``(status, age_seconds)`` without ever blocking on lnd.

    ``age_seconds`` is the age of the cached snapshot, or ``None`` while the cache
    is cold. When the cache is older than the TTL (or cold) a single background
    refresh is kicked off; the current call still returns immediately with the
    stale/``pending`` value.
    """
    # Default-off short-circuit: a disabled feature reports ``disabled`` instantly
    # (no network, no background task, no misleading ``pending``).
    if not get_settings().lightning.enabled:
        return LightningNodeStatus.disabled(), None
    if _cached is None:
        _start_refresh_if_idle()
        return _pending(), None
    age = time.monotonic() - _cached_at
    if age > _TTL_SECONDS:
        _start_refresh_if_idle()
    return _cached, age


def reset_cache_for_tests() -> None:
    """Clear module state (test seam only; not used in production paths)."""
    global _cached, _cached_at, _refresh_task
    _cached = None
    _cached_at = 0.0
    _refresh_task = None
