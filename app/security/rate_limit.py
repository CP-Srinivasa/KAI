"""Per-key sliding-window failure tracker for brute-force rate limiting.

D-193 / NEO-F-META-20260424-023 extraction. The original brute-force guard
(SENTR-F-003, D-156i) lives as module-level helpers in ``app/security/auth.py``
and tracks API-Key failures only. When we need the same semantics on another
auth surface — for example the TradingView webhook which has its own auth
pipeline (HMAC + shared-token) and would otherwise sit unprotected behind the
Cloudflare Tunnel — we want a separate failure bucket, not a shared singleton
that cross-pollutes API-Key and webhook-credential failures.

This module exposes:
  * :class:`FailureTracker` — instantiable, independent key spaces, same
    sliding-window semantics as the auth variant.
  * :func:`client_ip` — best-effort client IP resolver for FastAPI requests,
    honoring the Cloudflare and X-Forwarded-For headers used on the edge.

The ``app/security/auth.py`` variant is left in place on purpose — migrating
it would be a cross-cutting refactor for no functional gain. A later cleanup
can unify both usages behind this class.
"""

from __future__ import annotations

import time
from threading import Lock

from fastapi import Request


def client_ip(request: Request) -> str:
    """Best-effort client IP. Prefer Cf-Connecting-IP, then X-Forwarded-For.

    The order matches ``app/security/auth.py::_client_ip`` so both guards see
    the same identity for the same caller.
    """
    cf = request.headers.get("Cf-Connecting-IP", "").strip()
    if cf:
        return cf
    xff = request.headers.get("X-Forwarded-For", "").strip()
    if xff:
        return xff.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return ""


class FailureTracker:
    """Sliding-window per-key failure counter with lock-free reads.

    Usage::

        tracker = FailureTracker(window_seconds=300.0, threshold=10)
        locked, retry_after = tracker.is_limited(key)
        if locked:
            raise HTTPException(status_code=429, ...)
        # ... try auth ...
        if ok:
            tracker.reset(key)
        else:
            tracker.record_failure(key)

    Thread-safe via a single module-internal lock; the cost is insignificant
    against the wall-clock of the auth path itself.
    """

    def __init__(self, *, window_seconds: float, threshold: int) -> None:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        if threshold < 0:
            raise ValueError("threshold must be non-negative")
        self._window = window_seconds
        self._threshold = threshold
        self._failures: dict[str, list[float]] = {}
        self._lock = Lock()

    @property
    def threshold(self) -> int:
        return self._threshold

    @property
    def window_seconds(self) -> float:
        return self._window

    def _now(self) -> float:
        return time.monotonic()

    def is_limited(self, key: str, now: float | None = None) -> tuple[bool, int]:
        """Return (locked, retry_after_seconds) for ``key``.

        Locked when the in-window failure count has reached ``threshold``.
        ``retry_after`` is the remaining seconds until the oldest tracked
        failure ages out — after that point the count drops below threshold
        and the caller can try again.
        """
        if not key or self._threshold <= 0:
            return False, 0
        t = self._now() if now is None else now
        cutoff = t - self._window
        with self._lock:
            bucket = self._failures.get(key, [])
            bucket = [ts for ts in bucket if ts >= cutoff]
            if bucket:
                self._failures[key] = bucket
            else:
                self._failures.pop(key, None)
            if len(bucket) < self._threshold:
                return False, 0
            oldest = bucket[0]
        retry_after = max(1, int(oldest + self._window - t) + 1)
        return True, retry_after

    def record_failure(self, key: str, now: float | None = None) -> int:
        """Append a failure for ``key`` and return the in-window count."""
        if not key:
            return 0
        t = self._now() if now is None else now
        cutoff = t - self._window
        with self._lock:
            bucket = self._failures.setdefault(key, [])
            bucket[:] = [ts for ts in bucket if ts >= cutoff]
            bucket.append(t)
            return len(bucket)

    def reset(self, key: str) -> None:
        """Clear all failures for ``key`` — typically on a successful auth."""
        if not key:
            return
        with self._lock:
            self._failures.pop(key, None)

    def clear_all(self) -> None:
        """Test-only helper — drop every tracked key."""
        with self._lock:
            self._failures.clear()
