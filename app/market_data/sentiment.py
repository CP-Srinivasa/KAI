"""Crypto market-sentiment adapter — Fear & Greed Index (read-only, fail-closed).

Fetches the Crypto Fear & Greed Index from alternative.me — a FREE, public,
key-less JSON API (``https://api.alternative.me/fng/``). The provider URL is
FIXED (not attacker-controllable) → SSRF-safe, and there is no auth/scraping.

The dashboard reads a TTL-cached snapshot via :func:`get_cached_sentiment` so it
NEVER blocks on the external call and NEVER fabricates a value: when the fetch
fails or the cache is still cold, ``available`` is False and the panel shows an
honest "ausstehend" state instead of a number.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

_URL = "https://api.alternative.me/fng/?limit=1"
_TIMEOUT_SECONDS = 8.0
_TTL_SECONDS = 600.0  # F&G updates ~daily — a 10-minute cache is ample.

_cached: SentimentSnapshot | None = None
_cached_at: float = 0.0
_refresh_task: asyncio.Task[None] | None = None


@dataclass(frozen=True)
class SentimentSnapshot:
    """Crypto Fear & Greed snapshot. ``value`` is 0..100 (0 = extreme fear)."""

    available: bool
    value: int = 0
    classification: str = ""
    timestamp_utc: str = ""
    source: str = "alternative.me"
    reason: str = ""

    @classmethod
    def unavailable(cls, reason: str) -> SentimentSnapshot:
        return cls(available=False, reason=reason)


async def fetch_sentiment(
    transport: httpx.AsyncBaseTransport | None = None,
) -> SentimentSnapshot:
    """Fetch the current Fear & Greed value; never raises (fail-closed)."""
    try:
        kwargs: dict[str, Any] = {"timeout": _TIMEOUT_SECONDS}
        if transport is not None:
            kwargs["transport"] = transport
        async with httpx.AsyncClient(**kwargs) as client:
            resp = await client.get(_URL)
        if resp.status_code != 200:
            return SentimentSnapshot.unavailable(f"http {resp.status_code}")
        data = resp.json()
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list) or not rows:
            return SentimentSnapshot.unavailable("empty payload")
        row = rows[0]
        value = int(row["value"])
        ts_raw = row.get("timestamp")
        try:
            ts_utc = datetime.fromtimestamp(int(ts_raw), tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (TypeError, ValueError):
            ts_utc = str(ts_raw or "")
        return SentimentSnapshot(
            available=True,
            value=value,
            classification=str(row.get("value_classification", "")),
            timestamp_utc=ts_utc,
        )
    except Exception as exc:  # noqa: BLE001 — fail-closed, never raise into the request
        return SentimentSnapshot.unavailable(f"fetch failed: {exc}")


async def _refresh() -> None:
    global _cached, _cached_at
    snap = await fetch_sentiment()
    # Anti-flicker: keep the last GOOD value on a transient failure; only surface
    # an unavailable snapshot while the cache is still cold (no prior value).
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


async def get_cached_sentiment() -> tuple[SentimentSnapshot, float | None]:
    """Return ``(snapshot, age_seconds)`` without ever blocking on the provider."""
    if _cached is None:
        _start_refresh_if_idle()
        return SentimentSnapshot.unavailable("warming up"), None
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
