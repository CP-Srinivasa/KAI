"""symbol_eligibility_ledger — append-only JSONL of eligibility-verdict snapshots.

Read-only "Sicht"/audit artifact: each evaluation appends one snapshot line.
Mirrors ``momentum_universe_ledger`` (JSONL, no DB migration). No trade state.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from app.trading.symbol_eligibility import EligibilityVerdict


def eligibility_record(
    verdicts: Sequence[EligibilityVerdict], *, now: datetime
) -> dict[str, object]:
    """Build one snapshot record (pure; no I/O)."""
    return {
        "ts": now.isoformat(),
        "count": len(verdicts),
        "eligible_count": sum(1 for v in verdicts if v.eligible),
        "verdicts": [
            {"symbol": v.symbol, "eligible": v.eligible, "reasons": list(v.reasons)}
            for v in verdicts
        ],
    }


def append_eligibility_snapshot(
    path: Path, verdicts: Sequence[EligibilityVerdict], *, now: datetime
) -> dict[str, object]:
    """Append a snapshot line to ``path`` (creating parents). Returns the record."""
    record = eligibility_record(verdicts, now=now)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    return record


def read_latest_eligibility(path: Path) -> dict[str, object] | None:
    """Return the newest valid snapshot record, or ``None`` if missing/empty."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    latest: dict[str, object] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            latest = obj
    return latest
