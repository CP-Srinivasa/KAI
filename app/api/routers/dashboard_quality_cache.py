"""Single-flight TTL cache for expensive dashboard payload refreshes."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

type RefreshCallable = Callable[[], Awaitable[dict[str, Any]]]
type ClockCallable = Callable[[], float]
type NowUtcCallable = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class CacheMeta:
    """Metadata shipped with a cached response payload."""

    generated_at_utc: str
    age_s: float
    stale: bool
    ttl_s: float
    compute_ms: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "generated_at_utc": self.generated_at_utc,
            "age_s": self.age_s,
            "stale": self.stale,
            "ttl_s": self.ttl_s,
            "compute_ms": self.compute_ms,
        }


@dataclass(frozen=True)
class _CacheEntry:
    payload: dict[str, Any]
    at: float
    generated_at_utc: str
    compute_ms: float


class SingleFlightCache:
    """TTL cache with one in-flight refresh per event loop.

    Expired entries are normally refreshed by the first caller. Concurrent
    callers receive the previous entry as stale while that refresh is running;
    callers on an empty cache wait for the shared refresh task.
    """

    def __init__(
        self,
        refresh: RefreshCallable,
        ttl_s: float,
        include_cache_metadata: bool = True,
        *,
        clock: ClockCallable = time.monotonic,
        now_utc: NowUtcCallable = _utc_now,
    ) -> None:
        self._refresh = refresh
        self._ttl_s = ttl_s
        self._clock = clock
        self._now_utc = now_utc
        self._include_cache_metadata = include_cache_metadata

        self._entry: _CacheEntry | None = None
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        self._compute_task: asyncio.Task[None] | None = None
        self._compute_task_loop: asyncio.AbstractEventLoop | None = None
        self._stale_served_during_compute = False

    async def get(self) -> dict[str, Any]:
        """Return the cached payload, optionally with a ``cache`` metadata block."""
        loop = asyncio.get_running_loop()
        lock = self._singleflight_lock()
        while True:
            wait_task: asyncio.Task[None] | None = None
            async with lock:
                now = self._clock()
                if self._entry is not None and (now - self._entry.at) < self._ttl_s:
                    return self._payload_with_meta(entry=self._entry, now=now, stale=False)

                running_task = (
                    self._compute_task
                    if self._compute_task is not None
                    and self._compute_task_loop is loop
                    and not self._compute_task.done()
                    else None
                )
                if running_task is not None:
                    if self._entry is not None:
                        self._stale_served_during_compute = True
                        return self._payload_with_meta(entry=self._entry, now=now, stale=True)
                    wait_task = running_task
                else:
                    wait_task = asyncio.create_task(self._refresh_and_store())
                    self._compute_task = wait_task
                    self._compute_task_loop = loop

            assert wait_task is not None
            try:
                await wait_task
            finally:
                if wait_task.done():
                    async with lock:
                        if self._compute_task is wait_task:
                            self._compute_task = None
                            self._compute_task_loop = None

    def has_entry(self) -> bool:
        return self._entry is not None

    def _singleflight_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
            self._compute_task = None
            self._compute_task_loop = loop
        return self._lock

    async def _refresh_and_store(self) -> None:
        started = self._clock()
        try:
            payload = await self._refresh()
        except Exception:
            lock = self._singleflight_lock()
            async with lock:
                self._stale_served_during_compute = False
            raise

        compute_ms = (self._clock() - started) * 1000.0
        generated_at_utc = self._now_utc().isoformat()
        lock = self._singleflight_lock()
        async with lock:
            stale_served = self._stale_served_during_compute
            self._entry = _CacheEntry(
                payload=payload,
                at=self._clock(),
                generated_at_utc=generated_at_utc,
                compute_ms=compute_ms,
            )
            self._stale_served_during_compute = False

        logger.info(
            "single_flight_cache_refreshed compute_ms=%.0f stale_served=%s",
            compute_ms,
            stale_served,
        )

    def _payload_with_meta(self, *, entry: _CacheEntry, now: float, stale: bool) -> dict[str, Any]:
        response = dict(entry.payload)
        if not self._include_cache_metadata:
            return response
        response["cache"] = CacheMeta(
            generated_at_utc=entry.generated_at_utc,
            age_s=round(max(0.0, now - entry.at), 3),
            stale=stale,
            ttl_s=self._ttl_s,
            compute_ms=round(entry.compute_ms, 3),
        ).as_dict()
        return response
