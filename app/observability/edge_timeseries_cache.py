"""Background-refreshed cache for the edge-timeseries (#319).

:func:`app.observability.edge_timeseries.load_edge_timeseries` re-reads AND
re-parses the full resolved-ledger JSONL on every call. On a constrained host
(Raspberry Pi) that is >5s — too slow for a synchronous dashboard poll, and
worse: it is CPU/IO-bound *sync* work, so running it on the request coroutine
blocks the whole event loop for that duration (every other request stalls too).

Mirroring :mod:`app.chain.cache`, this decouples read from compute:

  * the dashboard reads :func:`get_cached_edge_timeseries` — it NEVER blocks on
    the ledger; it returns the last successfully computed series instantly (plus
    its age in seconds), or an empty series + ``age=None`` while the cache is
    still cold (honest "warming", never fabricated points);
  * a single in-flight background refresh recomputes the series when it goes
    stale. The heavy sync read runs via :func:`asyncio.to_thread` so it never
    blocks the event loop, and single-flight prevents pile-up under concurrency.

Honest by construction: it adds no new data, only caches what
``load_edge_timeseries`` already returns (thin windows still surface ``None``).
"""

from __future__ import annotations

import asyncio
import time

from app.observability.edge_timeseries import EdgeWindow, load_edge_timeseries

# Serve a cached series up to this age (seconds) before kicking a background
# refresh. The series buckets 7-day windows, so a few minutes of staleness is
# invisible; refreshes stay single-flight so a slow ledger cannot pile up.
_TTL_SECONDS = 300.0

_cached: list[EdgeWindow] | None = None
_cached_at: float = 0.0
_refresh_task: asyncio.Task[None] | None = None


async def _refresh() -> None:
    global _cached, _cached_at
    # The sync ledger read runs off the event loop; guard against any error so a
    # stray exception cannot kill the task and wedge the single-flight gate.
    try:
        windows = await asyncio.to_thread(load_edge_timeseries)
    except Exception:  # noqa: BLE001 — defensive; load_* is already fail-soft
        return
    _cached = windows
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


async def get_cached_edge_timeseries() -> tuple[list[EdgeWindow], float | None]:
    """Return ``(windows, age_seconds)`` without ever blocking on the ledger.

    ``age_seconds`` is the age of the cached series, or ``None`` while the cache
    is cold (warming). A cold-or-stale cache kicks a single background refresh;
    the current call still returns immediately with the stale/empty value.
    """
    if _cached is None:
        _start_refresh_if_idle()
        return [], None
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
