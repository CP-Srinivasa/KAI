"""Cross-platform exclusive file-lock context manager.

SAT-F-005 fix: O_APPEND atomicity ist nur für writes ≤ PIPE_BUF
(typisch 4 KB) garantiert.  Bayes-Reports + Thesis-Audit-Zeilen können
größer werden — Multi-Process-Append (CLI-Loop + FastAPI-Worker) kann
interleaved Bytes produzieren, die dann als "malformed" verworfen
werden (silent data loss).

Lösung: portabler exclusive-lock auf einem ``.lock``-Sidecar während
des Append.  Linux nutzt ``fcntl.flock``, Windows nutzt ``msvcrt.locking``.
Best-effort: bei Lock-Failure wird der Append trotzdem ausgeführt + ein
Warning geloggt — Audit-Trail darf den Trade-Pfad nicht blocken.
"""

from __future__ import annotations

import contextlib
import logging
import sys
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def append_lock(target: Path) -> Iterator[None]:
    """Hold an exclusive lock on ``target`` for the duration of an append.

    Lock-Sidecar lebt unter ``<target>.lock``.  Bei IO-/Lock-Fehler wird
    der Lock-Versuch geloggt + verschluckt; das ``with``-Block läuft
    trotzdem (fail-graceful).
    """
    lock_path = target.with_suffix(target.suffix + ".lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("[file-lock] cannot prepare %s: %s", lock_path, exc)
        yield
        return

    fh = None
    try:
        fh = lock_path.open("a+", encoding="utf-8")
    except OSError as exc:
        logger.warning("[file-lock] cannot open lockfile %s: %s", lock_path, exc)
        yield
        return

    locked = False
    try:
        if sys.platform.startswith("win"):
            try:
                import msvcrt

                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                locked = True
            except (OSError, ImportError) as exc:
                logger.warning("[file-lock] win32 lock failed for %s: %s", lock_path, exc)
        else:
            try:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                locked = True
            except (OSError, ImportError) as exc:
                logger.warning("[file-lock] posix lock failed for %s: %s", lock_path, exc)
        yield
    finally:
        if locked:
            try:
                if sys.platform.startswith("win"):
                    import msvcrt

                    # Reset position before unlock for msvcrt
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError as exc:
                logger.warning("[file-lock] unlock failed for %s: %s", lock_path, exc)
        try:
            fh.close()
        except OSError:
            pass


__all__ = ["append_lock"]
