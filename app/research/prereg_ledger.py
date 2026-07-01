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
    gate: dict[str, Any] | None = None,
) -> str:
    """Deterministic 16-hex key for a pre-registered claim (creation-time-agnostic).

    Whitespace/case-normalised so a trivially-reformatted re-registration of the
    same claim collapses to the same identity (and is thus detectable as a repeat).
    A machine-readable ``gate`` IS part of the claim: changing any threshold
    changes the identity. Gate-less (free-text-era) claims keep their old ids.
    """
    payload: dict[str, Any] = {
        "name": name.strip().lower(),
        "direction": direction.strip().lower(),
        "horizon": horizon.strip().lower(),
        "success_criteria": " ".join(success_criteria.split()).lower(),
        "sample_size_target": int(sample_size_target),
    }
    if gate is not None:
        payload["gate"] = gate
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
    # Optional machine-checkable pass bar (see app.research.prereg_gate); part of
    # the claim identity when present. None for free-text-era claims.
    gate: dict[str, Any] | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def from_dict(d: dict[str, Any]) -> PreRegistration:
        """Reconstruct from a parsed JSON object (explicit, typed coercion)."""
        gate = d.get("gate")
        return PreRegistration(
            prereg_id=str(d["prereg_id"]),
            name=str(d["name"]),
            direction=str(d["direction"]),
            horizon=str(d["horizon"]),
            success_criteria=str(d["success_criteria"]),
            sample_size_target=int(d["sample_size_target"]),
            created_at_utc=str(d["created_at_utc"]),
            schema=str(d.get("schema", SCHEMA)),
            gate=dict(gate) if isinstance(gate, dict) else None,
        )


def register(
    *,
    name: str,
    direction: str,
    horizon: str,
    success_criteria: str,
    sample_size_target: int,
    created_at_utc: str,
    gate: dict[str, Any] | None = None,
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
        gate=gate,
    )
    return PreRegistration(
        prereg_id=pid,
        name=name.strip(),
        direction=direction.strip().lower(),
        horizon=horizon.strip(),
        success_criteria=" ".join(success_criteria.split()),
        sample_size_target=int(sample_size_target),
        created_at_utc=created_at_utc,
        gate=gate,
    )


# --------------------------------------------------------------------------- #
# Canonical edge claim — the ONE pre-registration the edge-validation promotion
# gate (LIVE/capital) looks up. Shared by the gate (to find its prereg) and by
# the operator's registration call (to record the EXACT same claim), so the
# deterministic prereg_id can never drift between recording and lookup.
# --------------------------------------------------------------------------- #

CANONICAL_EDGE_NAME = "canonical_edge"
CANONICAL_EDGE_DIRECTION = "neutral"
CANONICAL_EDGE_HORIZON = "per_trade"


def canonical_edge_claim(*, min_n: int, confidence: float) -> dict[str, Any]:
    """The single falsifiable claim the LIVE/capital promotion gate tests.

    A neutral net-edge hypothesis whose pass bar mirrors the gate's own sample
    floor (``min_n``) and DSR/MinTRL confidence — pre-registering it commits in
    advance to the exact bar the gate later applies. Returns the kwargs accepted
    by :func:`prereg_key` and :func:`register`.
    """
    return {
        "name": CANONICAL_EDGE_NAME,
        "direction": CANONICAL_EDGE_DIRECTION,
        "horizon": CANONICAL_EDGE_HORIZON,
        "success_criteria": f"net_mean_bps>0 at n>={int(min_n)}, DSR>={confidence}",
        "sample_size_target": int(min_n),
    }


def canonical_edge_prereg_id(*, min_n: int, confidence: float) -> str:
    """Deterministic ``prereg_id`` for the canonical edge claim at these gate bars."""
    return prereg_key(**canonical_edge_claim(min_n=min_n, confidence=confidence))


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
    "CANONICAL_EDGE_DIRECTION",
    "CANONICAL_EDGE_HORIZON",
    "CANONICAL_EDGE_NAME",
    "DEFAULT_PREREG_LEDGER_PATH",
    "DIRECTIONS",
    "SCHEMA",
    "PreRegistration",
    "PreRegistrationLedger",
    "canonical_edge_claim",
    "canonical_edge_prereg_id",
    "prereg_key",
    "register",
]
