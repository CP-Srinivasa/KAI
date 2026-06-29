"""Pre-registration ledger — register a hypothesis BEFORE it is measured.

The hypothesis ledger (:mod:`app.research.ledger`) records hypotheses *after* a
run, carrying results. This ledger records a falsifiable claim *before* any data
is seen: its name, direction, horizon, success criteria, sample-size target and
creation time. That makes a later edge claim auditable against what was
pre-registered — the anti-p-hacking / garden-of-forking-paths discipline the
NORTH_STAR truth platform (ADR 0012) rests on.

Pre-registration is RECORD-ONLY and shadow: it gates nothing, blocks no trade,
and is never imported by the execution path. ``prereg_id`` is a deterministic
16-hex key over the claim itself (creation-time-agnostic), so an identical
re-registration is detectable while each row keeps its own ``created_at_utc``.

Read-only-safe: a corrupt line is skipped, never crashing a run.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Single source of truth for the ledger location; sits beside the hypothesis
# ledger and the falsification verdicts under artifacts/research/.
DEFAULT_PREREG_LEDGER_PATH = Path("artifacts/research/prereg_ledger.jsonl")

SCHEMA = "prereg/v1"

DIRECTIONS = ("long", "short", "neutral")


def prereg_key(
    *,
    name: str,
    direction: str,
    horizon: str,
    success_criteria: str,
    sample_size_target: int,
) -> str:
    """Deterministic 16-hex key for a pre-registered claim (creation-time-agnostic).

    Whitespace/case-normalised so a trivially-reformatted re-registration of the
    same claim collapses to the same identity (and is thus detectable as a repeat).
    """
    payload = {
        "name": name.strip().lower(),
        "direction": direction.strip().lower(),
        "horizon": horizon.strip().lower(),
        "success_criteria": " ".join(success_criteria.split()).lower(),
        "sample_size_target": int(sample_size_target),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class PreRegistration:
    """One pre-registered, falsifiable hypothesis (committed before measurement)."""

    prereg_id: str
    name: str
    direction: str
    horizon: str
    success_criteria: str
    sample_size_target: int
    created_at_utc: str
    schema: str = SCHEMA

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def from_dict(d: dict[str, Any]) -> PreRegistration:
        """Reconstruct from a parsed JSON object (explicit, typed coercion)."""
        return PreRegistration(
            prereg_id=str(d["prereg_id"]),
            name=str(d["name"]),
            direction=str(d["direction"]),
            horizon=str(d["horizon"]),
            success_criteria=str(d["success_criteria"]),
            sample_size_target=int(d["sample_size_target"]),
            created_at_utc=str(d["created_at_utc"]),
            schema=str(d.get("schema", SCHEMA)),
        )


def register(
    *,
    name: str,
    direction: str,
    horizon: str,
    success_criteria: str,
    sample_size_target: int,
    created_at_utc: str,
) -> PreRegistration:
    """Build a :class:`PreRegistration` with its deterministic ``prereg_id`` stamped.

    Does not write — the caller records it via :meth:`PreRegistrationLedger.record`.
    """
    pid = prereg_key(
        name=name,
        direction=direction,
        horizon=horizon,
        success_criteria=success_criteria,
        sample_size_target=sample_size_target,
    )
    return PreRegistration(
        prereg_id=pid,
        name=name.strip(),
        direction=direction.strip().lower(),
        horizon=horizon.strip(),
        success_criteria=" ".join(success_criteria.split()),
        sample_size_target=int(sample_size_target),
        created_at_utc=created_at_utc,
    )


class PreRegistrationLedger:
    """Append-only JSONL ledger of pre-registered hypotheses."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def record(self, entry: PreRegistration) -> None:
        """Append one pre-registration (creates parent dirs on first write)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(entry.to_json() + "\n")

    def entries(self) -> list[PreRegistration]:
        """All recorded pre-registrations (corrupt lines skipped, never raises)."""
        if not self._path.exists():
            return []
        out: list[PreRegistration] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, dict):
                    out.append(PreRegistration.from_dict(parsed))
            except (ValueError, TypeError, KeyError):
                continue  # a single bad line must never break a run
        return out

    def keys(self) -> set[str]:
        """Distinct pre-registration ids recorded so far."""
        return {e.prereg_id for e in self.entries()}

    def is_registered(self, prereg_id: str) -> bool:
        """True if this exact claim (by id) was pre-registered before."""
        return prereg_id in self.keys()

    def count(self) -> int:
        """Number of distinct pre-registered claims."""
        return len(self.keys())


__all__ = [
    "DEFAULT_PREREG_LEDGER_PATH",
    "DIRECTIONS",
    "SCHEMA",
    "PreRegistration",
    "PreRegistrationLedger",
    "prereg_key",
    "register",
]
