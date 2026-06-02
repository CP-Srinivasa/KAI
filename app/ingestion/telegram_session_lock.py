"""Single-owner lock for the Telegram listener session.

Why this module exists
----------------------
Telethon/MTProto allow only ONE live connection per ``.session`` auth key. If
the listener starts on two hosts (e.g. the Pi *and* a Windows dev box) against
the same session file, Telegram invalidates the key with
``AuthKeyDuplicatedError`` and BOTH connections die — a silent ingestion outage.
The documented KAI rule is "only the Pi owns the live session", but rules are
not enforcement.

This module provides a host-fingerprinted lock file so a second starter refuses
to boot instead of stealing the session. It is standalone (does not modify the
listener/worker) — the worker can call ``acquire`` at startup when adopted.

Contract
--------
- ``acquire`` writes a lock containing host, pid, and a monotonic-ish heartbeat
  timestamp. If a *fresh* lock owned by a different host/pid exists, it raises
  ``SessionLockError`` (fail-closed: do not start). A *stale* lock (heartbeat
  older than ``stale_after_seconds``) is considered abandoned and may be taken
  over (logged).
- Re-acquiring on the SAME host+pid is idempotent (restart-in-place is fine).
- ``heartbeat`` refreshes the timestamp; ``release`` removes the lock if owned.
- All IO is best-effort and explicit; a corrupt lock file is treated as stale
  rather than crashing the listener.
"""

from __future__ import annotations

import json
import logging
import os
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_STALE_AFTER_S = 1800  # 30 min — matches the heartbeat-stale alert window


class SessionLockError(RuntimeError):
    """Raised when a fresh lock owned by a different host/pid blocks startup."""


@dataclass(frozen=True)
class LockInfo:
    host: str
    pid: int
    acquired_utc: str
    heartbeat_utc: str

    def to_dict(self) -> dict[str, object]:
        return {
            "host": self.host,
            "pid": self.pid,
            "acquired_utc": self.acquired_utc,
            "heartbeat_utc": self.heartbeat_utc,
        }


def _host_fingerprint() -> str:
    return socket.gethostname()


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(UTC)


def _read_lock(path: Path) -> LockInfo | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        d = json.loads(raw)
        return LockInfo(
            host=str(d["host"]),
            pid=int(d["pid"]),
            acquired_utc=str(d["acquired_utc"]),
            heartbeat_utc=str(d["heartbeat_utc"]),
        )
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        # Corrupt lock — treat as stale/absent rather than crash the listener.
        logger.warning("[session-lock] corrupt lock file at %s — treating as stale", path)
        return None


def _is_stale(info: LockInfo, *, now: datetime, stale_after_seconds: int) -> bool:
    try:
        hb = datetime.fromisoformat(info.heartbeat_utc)
    except ValueError:
        return True
    if hb.tzinfo is None:
        hb = hb.replace(tzinfo=UTC)
    return (now - hb).total_seconds() > stale_after_seconds


def _write_lock(path: Path, info: LockInfo) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(info.to_dict()), encoding="utf-8")


def acquire(
    lock_path: str | Path,
    *,
    host: str | None = None,
    pid: int | None = None,
    now: datetime | None = None,
    stale_after_seconds: int = _DEFAULT_STALE_AFTER_S,
) -> LockInfo:
    """Acquire the session lock or raise SessionLockError.

    Same-host+pid re-acquire is idempotent. A foreign FRESH lock blocks startup;
    a STALE lock (heartbeat older than ``stale_after_seconds``) is taken over.
    """
    path = Path(lock_path)
    host = host or _host_fingerprint()
    pid = pid if pid is not None else os.getpid()
    ts = _now(now).isoformat()

    existing = _read_lock(path)
    if existing is not None:
        same_owner = existing.host == host and existing.pid == pid
        if not same_owner and not _is_stale(
            existing, now=_now(now), stale_after_seconds=stale_after_seconds
        ):
            raise SessionLockError(
                f"session locked by host={existing.host} pid={existing.pid} "
                f"(heartbeat={existing.heartbeat_utc}); refusing to start a second "
                f"listener on the same session — this would trigger "
                f"AuthKeyDuplicatedError"
            )
        if not same_owner:
            logger.warning(
                "[session-lock] taking over STALE lock from host=%s pid=%s (hb=%s)",
                existing.host,
                existing.pid,
                existing.heartbeat_utc,
            )

    info = LockInfo(host=host, pid=pid, acquired_utc=ts, heartbeat_utc=ts)
    _write_lock(path, info)
    return info


def heartbeat(
    lock_path: str | Path,
    *,
    host: str | None = None,
    pid: int | None = None,
    now: datetime | None = None,
) -> bool:
    """Refresh the heartbeat timestamp if this process owns the lock."""
    path = Path(lock_path)
    host = host or _host_fingerprint()
    pid = pid if pid is not None else os.getpid()
    existing = _read_lock(path)
    if existing is None or existing.host != host or existing.pid != pid:
        return False
    _write_lock(
        path,
        LockInfo(
            host=host,
            pid=pid,
            acquired_utc=existing.acquired_utc,
            heartbeat_utc=_now(now).isoformat(),
        ),
    )
    return True


def release(
    lock_path: str | Path, *, host: str | None = None, pid: int | None = None
) -> bool:
    """Remove the lock if owned by this host+pid."""
    path = Path(lock_path)
    host = host or _host_fingerprint()
    pid = pid if pid is not None else os.getpid()
    existing = _read_lock(path)
    if existing is None:
        return False
    if existing.host != host or existing.pid != pid:
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False


__all__ = [
    "LockInfo",
    "SessionLockError",
    "acquire",
    "heartbeat",
    "release",
]
