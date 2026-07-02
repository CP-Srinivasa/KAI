"""S-002 — L402 invoice-mint rate limiter (receive-side DoS guard).

The L402 challenge mints a REAL Lightning invoice on every unpaid request. Without
a cap, an unauthenticated flood would mint unbounded invoices (DoS / HTLC-flood
against the node). This limiter caps mints both **per key** (e.g. ``ip:scope``) and
**globally** within a fixed window, and MUST gate ``_issue_challenge`` before L402
is ever enabled.

In-memory, process-local (the oracle runs single-process). Fixed-window counters
with an injected clock (no wall-clock dependency → deterministic + testable). A
non-positive cap disables that dimension (limiter becomes a no-op for it).
"""

from __future__ import annotations


class MintLimiter:
    """Fixed-window per-key + global mint rate limiter."""

    def __init__(self, *, per_key_max: int, global_max: int, window_s: float = 60.0) -> None:
        self._per_key_max = per_key_max
        self._global_max = global_max
        self._window_s = window_s
        self._window_start: float = 0.0
        self._global_count: int = 0
        self._per_key: dict[str, int] = {}

    def _roll_window(self, now: float) -> None:
        if now - self._window_start >= self._window_s:
            self._window_start = now
            self._global_count = 0
            self._per_key.clear()

    def allow(self, key: str, *, now: float) -> bool:
        """Return True if a mint for ``key`` is permitted now, and count it; False if
        either the per-key or the global cap for the current window is exhausted.

        A non-positive cap means "unconfigured" → that dimension never blocks.
        """
        self._roll_window(now)
        if self._global_max > 0 and self._global_count >= self._global_max:
            return False
        if self._per_key_max > 0 and self._per_key.get(key, 0) >= self._per_key_max:
            return False
        self._global_count += 1
        self._per_key[key] = self._per_key.get(key, 0) + 1
        return True


__all__ = ["MintLimiter"]
