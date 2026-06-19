"""Perp-liquidation adapter — OKX public liquidation orders (read-only, fail-closed).

Aggregates recent perpetual-swap liquidations from OKX's FREE, public,
key-less endpoint ``/api/v5/public/liquidation-orders`` (fixed provider URL →
SSRF-safe, no auth/scraping). Per symbol we sum the liquidated size split by the
liquidated position side (``posSide`` long vs short) — a unit-free directional
pressure signal — plus an event count and the latest timestamp.

The dashboard reads a TTL-cached snapshot via :func:`get_cached_liquidations` so
it NEVER blocks on the provider and NEVER fabricates a value: on fetch failure or
cold cache, ``available`` is False and the panel shows an honest empty state.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

_BASE_URL = "https://www.okx.com/api/v5/public/liquidation-orders"
_INSTRUMENTS_URL = "https://www.okx.com/api/v5/public/instruments"
# OKX underlying → display symbol.
_UNDERLYINGS: tuple[str, ...] = ("BTC-USDT", "ETH-USDT", "SOL-USDT")
_TIMEOUT_SECONDS = 10.0
_TTL_SECONDS = 120.0  # liquidations are dynamic; a 2-minute cache stays gentle.

_cached: LiquidationsSnapshot | None = None
_cached_at: float = 0.0
_refresh_task: asyncio.Task[None] | None = None


@dataclass(frozen=True)
class LiquidationRow:
    """Aggregated recent liquidations for one symbol.

    ``*_sz`` is the raw OKX contract size; ``*_usd`` is the USD notional
    (``sz × ctVal × bkPx``) — present when the instrument's ctVal was resolved.
    """

    symbol: str
    long_sz: float  # liquidated LONG positions (posSide=long), contracts
    short_sz: float  # liquidated SHORT positions (posSide=short), contracts
    long_usd: float  # USD notional of liquidated LONGs
    short_usd: float  # USD notional of liquidated SHORTs
    events: int
    last_ts_utc: str


@dataclass(frozen=True)
class LiquidationsSnapshot:
    available: bool
    rows: tuple[LiquidationRow, ...] = field(default_factory=tuple)
    source: str = "okx"
    reason: str = ""

    @classmethod
    def unavailable(cls, reason: str) -> LiquidationsSnapshot:
        return cls(available=False, reason=reason)


def _ts_utc(ms: int) -> str:
    try:
        return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError):
        return ""


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _aggregate(underlying: str, payload: Any, ct_val: float) -> LiquidationRow | None:
    """Aggregate one symbol's liquidations. ``ct_val`` (contracts→base coin)
    turns each ``sz × bkPx`` into USD notional; 0 if ctVal was unresolved."""
    if not isinstance(payload, dict) or payload.get("code") != "0":
        return None
    details: list[dict[str, Any]] = []
    for inst in payload.get("data", []) or []:
        if isinstance(inst, dict):
            details.extend(d for d in (inst.get("details") or []) if isinstance(d, dict))
    symbol = underlying.replace("-USDT", "/USDT")
    if not details:
        return LiquidationRow(symbol, 0.0, 0.0, 0.0, 0.0, 0, "")

    long_sz = short_sz = long_usd = short_usd = 0.0
    for d in details:
        sz = _f(d.get("sz"))
        usd = sz * ct_val * _f(d.get("bkPx"))
        if d.get("posSide") == "long":
            long_sz += sz
            long_usd += usd
        elif d.get("posSide") == "short":
            short_sz += sz
            short_usd += usd
    last_ms = max((int(d.get("ts", 0) or 0) for d in details), default=0)
    return LiquidationRow(
        symbol=symbol,
        long_sz=round(long_sz, 4),
        short_sz=round(short_sz, 4),
        long_usd=round(long_usd, 2),
        short_usd=round(short_usd, 2),
        events=len(details),
        last_ts_utc=_ts_utc(last_ms),
    )


async def _fetch_ct_vals(client: httpx.AsyncClient) -> dict[str, float]:
    """Resolve ctVal (contract size in base coin) per underlying; best-effort.

    Returns a partial/empty map on failure — a missing ctVal simply yields 0 USD
    notional for that symbol (the raw ``sz`` stays correct), never an error.
    """
    out: dict[str, float] = {}
    resps = await asyncio.gather(
        *(
            client.get(_INSTRUMENTS_URL, params={"instType": "SWAP", "instId": f"{uly}-SWAP"})
            for uly in _UNDERLYINGS
        ),
        return_exceptions=True,
    )
    for uly, resp in zip(_UNDERLYINGS, resps, strict=True):
        if isinstance(resp, BaseException) or resp.status_code != 200:
            continue
        try:
            data = resp.json()
            rows = data.get("data") if isinstance(data, dict) else None
            if rows:
                out[uly] = _f(rows[0].get("ctVal"))
        except Exception:  # noqa: BLE001 — best-effort enrichment
            continue
    return out


async def fetch_liquidations(
    transport: httpx.AsyncBaseTransport | None = None,
) -> LiquidationsSnapshot:
    """Fetch + aggregate recent OKX liquidations; never raises (fail-closed)."""
    try:
        kwargs: dict[str, Any] = {"timeout": _TIMEOUT_SECONDS}
        if transport is not None:
            kwargs["transport"] = transport
        async with httpx.AsyncClient(**kwargs) as client:
            liq_responses, ct_vals = await asyncio.gather(
                asyncio.gather(
                    *(
                        client.get(
                            _BASE_URL,
                            params={"instType": "SWAP", "state": "filled", "uly": uly},
                        )
                        for uly in _UNDERLYINGS
                    ),
                    return_exceptions=True,
                ),
                _fetch_ct_vals(client),
            )
        rows: list[LiquidationRow] = []
        for uly, resp in zip(_UNDERLYINGS, liq_responses, strict=True):
            if isinstance(resp, BaseException) or resp.status_code != 200:
                continue
            row = _aggregate(uly, resp.json(), ct_vals.get(uly, 0.0))
            if row is not None:
                rows.append(row)
        if not rows:
            return LiquidationsSnapshot.unavailable("no liquidation data")
        return LiquidationsSnapshot(available=True, rows=tuple(rows))
    except Exception as exc:  # noqa: BLE001 — fail-closed, never raise into the request
        return LiquidationsSnapshot.unavailable(f"fetch failed: {exc}")


async def _refresh() -> None:
    global _cached, _cached_at
    snap = await fetch_liquidations()
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


async def get_cached_liquidations() -> tuple[LiquidationsSnapshot, float | None]:
    """Return ``(snapshot, age_seconds)`` without ever blocking on the provider."""
    if _cached is None:
        _start_refresh_if_idle()
        return LiquidationsSnapshot.unavailable("warming up"), None
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
