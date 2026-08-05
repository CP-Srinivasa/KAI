"""Persistent, bounded idempotency-key ledger for the value-layer confirm gate.

The B-005 confirm gate (:mod:`app.lightning.control_gate`) rejects a replayed
idempotency key so a resubmitted irreversible execute cannot double-spend. That
guard is only as strong as the memory of consumed keys: a process-local ``set`` is
emptied on every restart, re-opening the replay window (a confirmed send + a crash
or redeploy + a replayed request = a second spend).

:class:`PersistentSeenKeys` persists consumed keys to a small JSONL file, bounded to
the most recent ``max_keys`` entries (each with a timestamp). It subclasses
``set[str]`` so it drops straight into ``verify_capital_confirm(seen_keys=...)``
without touching that security-core signature: ``key in store`` is the in-memory
membership check, ``store.add(key)`` consumes + persists.

Only ``add`` / ``__contains__`` / ``clear`` are persistence-aware — the other set
mutators (``discard``, ``pop``, ``update``, ...) are intentionally NOT supported by
this ledger and would desync the on-disk view; the confirm gate never uses them.

Reads stay fail-soft (a missing or corrupt ledger loads as empty), but CONSUMING a
key is fail-CLOSED: the key becomes visible in memory only after the bounded file was
atomically replaced, and a persist error raises :class:`IdempotencyPersistenceError`.
Swallowing that error was the dangerous half of the old behaviour — the caller would
believe it holds a restart-safe replay guard while the disk knows nothing, so a
confirmed spend + a restart + a replayed request could pay twice. A raised error at
the gate is a denial of the value action; the money never moves without a durable
guard. Writes are atomic via temp-file + fsync + ``os.replace`` full rewrite — cheap
because confirms are rare (operator HOTP per irreversible spend) and bounded.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path

from app.storage.jsonl_io import iter_jsonl_tolerant

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path("artifacts/ln_idempotency_seen.jsonl")
_DEFAULT_MAX_KEYS = 1000


class IdempotencyPersistenceError(RuntimeError):
    """A key could not be durably consumed; the value action must not execute."""


class PersistentSeenKeys(set[str]):
    """A ``set[str]`` of consumed idempotency keys, persisted + bounded on disk."""

    def __init__(self, path: Path | None = None, *, max_keys: int = _DEFAULT_MAX_KEYS) -> None:
        super().__init__()
        self._path = path or _DEFAULT_PATH
        self._max_keys = max(1, max_keys)
        # One consumer at a time: check-and-consume must be atomic, otherwise two
        # concurrent requests with the same key both pass the membership check.
        self._lock = threading.Lock()
        # Insertion-ordered key -> ISO-8601 ts. Source of truth for both the recency
        # bound (evict oldest) and persistence; the set itself is the membership index.
        self._records: OrderedDict[str, str] = OrderedDict()
        self._load()

    def _load(self) -> None:
        """Rebuild in-memory state from the JSONL file (tolerant, bounded)."""
        for rec in iter_jsonl_tolerant(self._path):
            key = rec.get("key")
            if not isinstance(key, str) or not key:
                continue
            ts = rec.get("ts")
            self._records[key] = ts if isinstance(ts, str) else ""
        while len(self._records) > self._max_keys:
            self._records.popitem(last=False)
        super().clear()
        super().update(self._records.keys())

    def add(self, key: str) -> None:
        """Consume ``key`` (``set``-compatible seam used by the B-005 confirm gate).

        Raises :class:`IdempotencyPersistenceError` when the key cannot be durably
        recorded — the caller must then deny the value action.
        """
        self.consume(key)

    def consume(self, key: str) -> bool:
        """Atomically check-and-durably-consume ``key``; ``False`` if already seen.

        The in-memory view is advanced only AFTER the on-disk replacement succeeded,
        so a failed persist can never create a false sense of restart-safe
        idempotency. Adding a new key past ``max_keys`` evicts the oldest so both the
        set and the file stay bounded.
        """
        with self._lock:
            if key in self._records:
                return False
            records = OrderedDict(self._records)
            records[key] = datetime.now(UTC).isoformat()
            while len(records) > self._max_keys:
                records.popitem(last=False)
            self._persist(records)
            self._records = records
            super().clear()
            super().update(records.keys())
            return True

    def clear(self) -> None:
        """Forget every key in memory AND on disk (test seam / operator reset)."""
        with self._lock:
            self._persist(OrderedDict())
            self._records.clear()
            super().clear()

    def _persist(self, records: OrderedDict[str, str]) -> None:
        """Atomically rewrite the bounded ledger (temp file + fsync + ``os.replace``)."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(f"{self._path.name}.tmp.{os.getpid()}")
            with tmp.open("w", encoding="utf-8") as handle:
                for key, ts in records.items():
                    handle.write(json.dumps({"key": key, "ts": ts}, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self._path)
        except OSError as exc:  # fail-closed: no durable guard → no value action
            logger.warning("[ln-idempotency] persist failed: %s", exc)
            raise IdempotencyPersistenceError(
                f"idempotency ledger unavailable: {type(exc).__name__}"
            ) from exc


__all__ = ["IdempotencyPersistenceError", "PersistentSeenKeys"]
