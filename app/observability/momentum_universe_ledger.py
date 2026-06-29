"""momentum_universe_ledger — append-only JSONL of ranked-universe snapshots.

A read-only "Sicht" artifact: each (re-)rank appends one snapshot line; the
dashboard/API reads the latest. No trade state, no capital effect. Mirrors the
shadow-ledger pattern used elsewhere (JSONL, no DB migration).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

from app.observability.momentum_universe import RankedSymbol


def snapshot_record(
    ranked: Sequence[RankedSymbol],
    *,
    now: datetime,
    eligibility: Mapping[str, dict[str, object]] | None = None,
) -> dict[str, object]:
    """Build one snapshot record (pure; no I/O).

    When ``eligibility`` is given, each row for a symbol present in it gains
    ``eligible``/``reasons`` (shadow flag, additive — never filters here).
    """

    def _row(r: RankedSymbol) -> dict[str, object]:
        row: dict[str, object] = {
            "symbol": r.symbol,
            "rank": r.rank,
            "universe_score": round(r.universe_score, 6),
            "volume_score": round(r.volume_score, 6),
            "momentum_score": round(r.momentum_score, 6),
        }
        if eligibility is not None and r.symbol in eligibility:
            verdict = eligibility[r.symbol]
            row["eligible"] = verdict.get("eligible")
            row["reasons"] = verdict.get("reasons", [])
        return row

    return {
        "ts": now.isoformat(),
        "count": len(ranked),
        "universe": [_row(r) for r in ranked],
    }


def append_snapshot(
    path: Path,
    ranked: Sequence[RankedSymbol],
    *,
    now: datetime,
    eligibility: Mapping[str, dict[str, object]] | None = None,
) -> dict[str, object]:
    """Append a snapshot line to ``path`` (creating parents). Returns the record."""
    record = snapshot_record(ranked, now=now, eligibility=eligibility)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    return record


def read_latest(path: Path) -> dict[str, object] | None:
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
