"""Tolerant tail reader for append-only JSONL shadow streams (read-only).

Shared by the Lightning observability read paths (reputation telemetry, ops
ledger). A missing file → ``[]``; blank or corrupt lines are skipped rather than
raising, so a half-written last line never breaks a dashboard read. ``limit``
counts ACTUAL records (a trailing blank/corrupt line must not consume a slot), so
filtering happens before the last-N slice.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_recent_jsonl(path: Path, *, limit: int = 200) -> list[dict[str, Any]]:
    """Return up to the last ``limit`` valid JSON-object records (newest last)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out[-limit:] if limit > 0 else out
