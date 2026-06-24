"""Sprint 6 — SCB (channel.backup) drift monitor (resilience, read-only).

The lnd static channel backup (SCB) must be re-archived whenever channels change
(open/close), or a recovery would miss funds. This module hashes the SCB and
compares it to a recorded baseline, surfacing an operator re-backup reminder on
drift. Pure file I/O, fail-soft, NO capital path.

States: ``missing`` (SCB gone), ``no_baseline`` (first run → records it),
``stable`` (matches), ``changed`` (differs → reminder; baseline advanced).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCB_BASELINE_PATH = Path("artifacts/scb_baseline.json")


@dataclass(frozen=True)
class ScbStatus:
    present: bool
    size_bytes: int = 0
    sha256: str = ""
    mtime_iso: str = ""


def read_scb_status(scb_path: Path | str) -> ScbStatus:
    """Hash + stat the SCB file (``present=False`` if missing/unreadable)."""
    p = Path(scb_path)
    try:
        raw = p.read_bytes()
        st = p.stat()
    except OSError:
        return ScbStatus(present=False)
    return ScbStatus(
        present=True,
        size_bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        mtime_iso=datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat(),
    )


def _read_baseline(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_baseline(path: Path, status: ScbStatus) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "sha256": status.sha256,
                    "size_bytes": status.size_bytes,
                    "recorded_at": datetime.now(UTC).isoformat(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("[scb-monitor] baseline write failed: %s", exc)


def check_scb_drift(
    scb_path: Path | str, *, baseline_path: Path | str | None = None
) -> dict[str, Any]:
    """Compare the SCB against the recorded baseline; advance the baseline on a
    legitimate change so the next run is ``stable`` again. Never raises."""
    base = Path(baseline_path) if baseline_path is not None else _SCB_BASELINE_PATH
    status = read_scb_status(scb_path)
    if not status.present:
        return {"state": "missing", "reminder": True, "sha256": "", "detail": "SCB file not found"}

    baseline = _read_baseline(base)
    prev = str(baseline.get("sha256", ""))
    if not prev:
        _write_baseline(base, status)
        return {"state": "no_baseline", "reminder": False, "sha256": status.sha256}
    if prev == status.sha256:
        return {"state": "stable", "reminder": False, "sha256": status.sha256}
    _write_baseline(base, status)
    return {
        "state": "changed",
        "reminder": True,
        "sha256": status.sha256,
        "previous_sha256": prev,
        "detail": "SCB changed (channels opened/closed?) — re-archive the backup",
    }


__all__ = ["ScbStatus", "check_scb_drift", "read_scb_status"]
