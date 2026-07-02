"""Background-refreshed cache for the replay-SSOT status (#314).

:func:`app.observability.replay_status.load_replay_status` replays the full
``paper_execution_audit.jsonl`` (parse every line + reconstruct positions) on
every call — on a constrained host (Raspberry Pi) that is slow, and as *sync*
CPU/IO work on the request coroutine it blocks the whole event loop for that
duration (every other request stalls too). Same hazard the edge-timeseries
endpoint hit; solved the same way.

Mirroring :mod:`app.observability.edge_timeseries_cache` / :mod:`app.chain.cache`:
serve the last computed status instantly, recompute in the background via
:func:`asyncio.to_thread` (off the event loop), single-flight under concurrency,
TTL 300s. Cold start returns ``(None, None)`` so the endpoint can report an
honest ``warming`` state instead of blocking.
"""

from __future__ import annotations

import asyncio
import time

from app.observability.replay_status import ReplayStatus, load_replay_status

_TTL_SECONDS = 300.0

_cached: ReplayStatus | None = None
_cached_at: float = 0.0
_refresh_task: asyncio.Task[None] | None = None


async def _refresh() -> None:
    global _cached, _cached_at
    # Der sync Replay-Read läuft off the event loop; gegen jeden Fehler abgesichert,
    # damit eine Ausnahme das Single-Flight-Gate nicht verklemmt.
    try:
        status = await asyncio.to_thread(load_replay_status)
    except Exception:  # noqa: BLE001 — defensiv; load_* ist bereits fail-soft
        return
    _cached = status
    _cached_at = time.monotonic()


def _start_refresh_if_idle() -> None:
    """Startet genau einen Hintergrund-Refresh; no-op wenn bereits einer läuft.

    Sicher unter einem Event-Loop: zwischen In-Flight-Check und ``create_task``
    liegt kein ``await``, also können nicht zwei Aufrufer parallel starten.
    """
    global _refresh_task
    if _refresh_task is not None and not _refresh_task.done():
        return
    _refresh_task = asyncio.create_task(_refresh())


async def get_cached_replay_status() -> tuple[ReplayStatus | None, float | None]:
    """Liefert ``(status, age_seconds)`` ohne je auf dem Replay zu blockieren.

    ``status`` ist ``None`` solange der Cache kalt ist (warming); ``age_seconds``
    ist dann ebenfalls ``None``. Ein kalter-oder-veralteter Cache stößt genau
    einen Hintergrund-Refresh an; der aktuelle Aufruf liefert sofort den
    stale/leeren Wert.
    """
    if _cached is None:
        _start_refresh_if_idle()
        return None, None
    age = time.monotonic() - _cached_at
    if age > _TTL_SECONDS:
        _start_refresh_if_idle()
    return _cached, age


def reset_cache_for_tests() -> None:
    """Modul-State leeren (nur Test-Seam; kein Produktionspfad)."""
    global _cached, _cached_at, _refresh_task
    _cached = None
    _cached_at = 0.0
    _refresh_task = None
