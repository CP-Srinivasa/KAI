"""Binance USDT-M all-market liquidation stream consumer (read-only canary).

#316 Phase 1. Long-lived WebSocket consumer for ``!forceOrder@arr`` that
normalizes each force-order into a canonical ``LiquidationEvent`` and appends it
to the ledger. READ-ONLY: it observes and records. It never opens, sizes, gates
or blocks a trade.

Run as a systemd service::

    python -m app.ingestion.liquidations.binance_stream

Test seams keep it offline-verifiable:
- ``process_raw``  — parse one raw message → append (pure-ish, no socket).
- ``_consume``     — drain an (async-iterable) ws → ledger + heartbeat.
- ``run(..., stop_after_disconnects=N)`` — bounded reconnect loop for tests.

The all-market stream pushes only the LARGEST liquidation per symbol per
1000 ms, so events are flagged ``is_snapshot_limited`` upstream — the canary
under-counts true pressure (good enough for stress/cascade visibility, not for
market-wide attribution).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from app.ingestion.liquidations.binance_forceorder import normalize_forceorder
from app.market_data.liquidation_ledger import DEFAULT_PATH, append_event

logger = structlog.get_logger(__name__)

STREAM_URL = "wss://fstream.binance.com/ws/!forceOrder@arr"
HEARTBEAT_PATH = Path("artifacts/liquidation_stream_heartbeat.txt")
_HEARTBEAT_MIN_INTERVAL_S = 5.0
_MAX_BACKOFF_S = 60.0

_last_heartbeat_monotonic = 0.0


def process_raw(raw: str | bytes, ledger_path: Path = DEFAULT_PATH) -> bool:
    """Parse + normalize + append one raw message. Returns True if an event was
    written. Fail-open: bad JSON / non-liquidation frames are ignored, not raised."""
    try:
        payload: Any = json.loads(raw)
    except (ValueError, TypeError):
        return False
    if not isinstance(payload, dict):
        return False
    event = normalize_forceorder(payload)
    if event is None:
        return False
    append_event(event, ledger_path)
    return True


def write_heartbeat(path: Path = HEARTBEAT_PATH, *, force: bool = False) -> None:
    """Throttled liveness marker so the dashboard can tell 'connected but calm'
    (idle) from 'feed down'. Throttled to one write per ``_HEARTBEAT_MIN_INTERVAL_S``."""
    global _last_heartbeat_monotonic
    now = time.monotonic()
    if not force and (now - _last_heartbeat_monotonic) < _HEARTBEAT_MIN_INTERVAL_S:
        return
    _last_heartbeat_monotonic = now
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
    except OSError:
        pass  # heartbeat is best-effort; never crash the consumer over it


_HEARTBEAT_TICK_S = 15.0


async def _consume(
    ws: Any,
    ledger_path: Path,
    heartbeat_path: Path,
    *,
    heartbeat_tick_s: float = _HEARTBEAT_TICK_S,
) -> None:
    """Drain messages from a connected ws into the ledger; heartbeat each frame.

    A concurrent ticker also refreshes the heartbeat every ``heartbeat_tick_s``
    while connected — ``!forceOrder@arr`` is silent during calm markets (minutes
    with zero messages), so a message-only heartbeat would go stale and the
    dashboard would falsely report the feed as 'down'. The ticker is bound to the
    connection lifetime: it is cancelled the moment the stream ends/errors, so a
    real disconnect still lets the heartbeat go stale (honest 'down')."""
    write_heartbeat(heartbeat_path, force=True)  # mark 'connected' immediately

    async def _ticker() -> None:
        while True:
            await asyncio.sleep(heartbeat_tick_s)
            write_heartbeat(heartbeat_path, force=True)

    ticker = asyncio.create_task(_ticker())
    try:
        async for raw in ws:
            process_raw(raw, ledger_path)
            write_heartbeat(heartbeat_path)
    finally:
        ticker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ticker


async def run(
    *,
    url: str = STREAM_URL,
    ledger_path: Path = DEFAULT_PATH,
    heartbeat_path: Path = HEARTBEAT_PATH,
    connect: Any = None,
    stop_after_disconnects: int | None = None,
    max_backoff_s: float = _MAX_BACKOFF_S,
) -> None:
    """Reconnecting consumer loop. ``connect`` defaults to ``websockets.connect``
    (imported lazily so the module imports without the optional dep). Runs forever
    unless ``stop_after_disconnects`` is set (test seam)."""
    if connect is None:
        import websockets  # transitive via uvicorn[standard]

        connect = websockets.connect

    disconnects = 0
    backoff = 1.0
    while True:
        try:
            async with connect(url) as ws:
                logger.info("liquidation_stream_connected", url=url)
                backoff = 1.0
                await _consume(ws, ledger_path, heartbeat_path)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — reconnect on any stream error
            logger.warning("liquidation_stream_error", error=str(exc))
        disconnects += 1
        if stop_after_disconnects is not None and disconnects >= stop_after_disconnects:
            return
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, max_backoff_s)


def main() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())


if __name__ == "__main__":
    main()
