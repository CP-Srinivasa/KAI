"""Price-momentum adapter — Binance 24h ticker (read-only, fail-closed).

Reads the 24-hour price change from Binance's FREE, public, key-less endpoint
``/api/v3/ticker/24hr`` (fixed provider URL → SSRF-safe, no auth/scraping) for a
small symbol set. ``change_pct_24h`` is the real 24h % move — a simple, honest
momentum read, no derived/black-box score.

The dashboard reads a TTL-cached snapshot via :func:`get_cached_momentum` so it
NEVER blocks on the provider and NEVER fabricates a value: on fetch failure or a
cold cache, ``available`` is False.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

_BASE_URL = "https://api.binance.com/api/v3/ticker/24hr"
# Binance symbol → display symbol.
_SYMBOLS: dict[str, str] = {"BTCUSDT": "BTC/USDT", "ETHUSDT": "ETH/USDT", "SOLUSDT": "SOL/USDT"}
_TIMEOUT_SECONDS = 8.0
_TTL_SECONDS = 60.0

_cached: MomentumSnapshot | None = None
_cached_at: float = 0.0
_refresh_task: asyncio.Task[None] | None = None


@dataclass(frozen=True)
class MomentumRow:
    symbol: str
    last_price: float
    change_pct_24h: float


@dataclass(frozen=True)
class MomentumSnapshot:
    available: bool
    rows: tuple[MomentumRow, ...] = field(default_factory=tuple)
    source: str = "binance"
    reason: str = ""

    @classmethod
    def unavailable(cls, reason: str) -> MomentumSnapshot:
        return cls(available=False, reason=reason)


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


async def fetch_momentum(
    transport: httpx.AsyncBaseTransport | None = None,
) -> MomentumSnapshot:
    """Fetch Binance 24h change for the tracked symbols; never raises."""
    try:
        kwargs: dict[str, Any] = {"timeout": _TIMEOUT_SECONDS}
        if transport is not None:
            kwargs["transport"] = transport
        symbols_param = "[" + ",".join(f'"{s}"' for s in _SYMBOLS) + "]"
        async with httpx.AsyncClient(**kwargs) as client:
            resp = await client.get(_BASE_URL, params={"symbols": symbols_param})
        if resp.status_code != 200:
            return MomentumSnapshot.unavailable(f"http {resp.status_code}")
        data = resp.json()
        if not isinstance(data, list) or not data:
            return MomentumSnapshot.unavailable("empty payload")
        rows: list[MomentumRow] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            disp = _SYMBOLS.get(str(entry.get("symbol", "")))
            if disp is None:
                continue
            rows.append(
                MomentumRow(
                    symbol=disp,
                    last_price=_f(entry.get("lastPrice")),
                    change_pct_24h=_f(entry.get("priceChangePercent")),
                )
            )
        if not rows:
            return MomentumSnapshot.unavailable("no tracked symbols in payload")
        return MomentumSnapshot(available=True, rows=tuple(rows))
    except Exception as exc:  # noqa: BLE001 — fail-closed, never raise into the request
        return MomentumSnapshot.unavailable(f"fetch failed: {exc}")


async def _refresh() -> None:
    global _cached, _cached_at
    snap = await fetch_momentum()
    if snap.available:
        _cached = snap
        _cached_at = time.monotonic()
    elif _cached is None:
        _cached = snap


def _start_refresh_if_idle() -> None:
    global _refresh_task
    if _refresh_task is not None and not _refresh_task.done():
        return
    _refresh_task = asyncio.create_task(_refresh())


async def get_cached_momentum() -> tuple[MomentumSnapshot, float | None]:
    """Return ``(snapshot, age_seconds)`` without ever blocking on the provider."""
    if _cached is None:
        _start_refresh_if_idle()
        return MomentumSnapshot.unavailable("warming up"), None
    age = time.monotonic() - _cached_at
    if age > _TTL_SECONDS:
        _start_refresh_if_idle()
    return _cached, age


def reset_cache_for_tests() -> None:
    """Clear module state (test seam only)."""
    global _cached, _cached_at, _refresh_task
    _cached = None
    _cached_at = 0.0
    _refresh_task = None
