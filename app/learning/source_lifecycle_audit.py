"""Append-only audit trail for source-lifecycle transitions.

One JSONL line per status/tier change so every autonomous rotation, onboarding,
silencing, pin and archival is accountable — "only logging" means THIS append is
the record. Corrupt lines are skipped on read; the writer never crashes a recalc.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.storage.jsonl_io import read_jsonl_tolerant

LIFECYCLE_AUDIT_FILENAME = "source_lifecycle_audit.jsonl"


@dataclass(frozen=True)
class LifecycleEvent:
    """One recorded source-lifecycle transition."""

    source: str
    from_status: str
    to_status: str
    reason: str
    recorded_at_utc: str
    evidence: dict[str, Any] | None = None


def _resolve(path: Path) -> Path:
    return path / LIFECYCLE_AUDIT_FILENAME if path.is_dir() else path


def append_lifecycle_event(event: LifecycleEvent, path: Path) -> None:
    """Append one event as a JSONL line (creates parent dirs on first write)."""
    target = _resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(event), ensure_ascii=False, sort_keys=True) + "\n")


def read_lifecycle_events(path: Path) -> list[LifecycleEvent]:
    """Read all events (corrupt / missing-field lines skipped, never raises)."""
    target = _resolve(path)
    if not target.exists():
        return []
    out: list[LifecycleEvent] = []
    for row in read_jsonl_tolerant(target):
        try:
            evidence = row.get("evidence")
            out.append(
                LifecycleEvent(
                    source=str(row["source"]),
                    from_status=str(row["from_status"]),
                    to_status=str(row["to_status"]),
                    reason=str(row.get("reason", "")),
                    recorded_at_utc=str(row["recorded_at_utc"]),
                    evidence=evidence if isinstance(evidence, dict) else None,
                )
            )
        except (KeyError, TypeError):
            continue
    return out
