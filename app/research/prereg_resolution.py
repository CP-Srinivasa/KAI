"""Pre-registration RESOLUTION — close a pre-registered claim against measurement.

The pre-registration ledger (:mod:`app.research.prereg_ledger`) records a
falsifiable claim BEFORE data is seen. This module records the OTHER half of the
falsification loop ADR 0012 rests on: the VERDICT once the data is in — did the
measurement MEET the pre-committed bar, fail it (``NOT_MET``), or is the sample
still too small to conclude (``INSUFFICIENT_N``)?

Resolution is RECORD-ONLY and shadow: it gates nothing, blocks no trade, and is
never imported by the execution path. It appends one auditable row per resolution
so the ledger becomes register → measure → verdict, fully re-derivable.

:func:`resolve_canonical` is PURE (verdict in, :class:`Resolution` out) so the
decision rule is unit-testable without the measurement machinery; the runner
(``scripts/prereg_resolve.py``) supplies the live
:class:`~app.observability.edge_validation_gate.EdgeValidationVerdict`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.observability.edge_validation_gate import EdgeValidationVerdict

RESOLUTION_SCHEMA = "prereg_resolution/v1"

# Sits beside the pre-registration ledger and the hypothesis ledger under
# artifacts/research/; distinct from the tamper-evident falsification-verdict
# ledger (app.observability.falsification_verdict), which attests existence-time,
# not pre-registration linkage.
DEFAULT_PREREG_VERDICTS_PATH = Path("artifacts/research/prereg_verdicts.jsonl")

VERDICT_MET = "MET"
VERDICT_NOT_MET = "NOT_MET"
VERDICT_INSUFFICIENT_N = "INSUFFICIENT_N"
VERDICTS = (VERDICT_MET, VERDICT_NOT_MET, VERDICT_INSUFFICIENT_N)


@dataclass(frozen=True)
class Resolution:
    """One recorded resolution of a pre-registered claim (the verdict half)."""

    prereg_id: str
    name: str
    verdict: str
    measured_n: int
    sample_size_target: int
    mean_net_bps: float
    deflated_sharpe: float | None
    ready: bool
    reason: str
    resolved_at_utc: str
    source: str = "canonical_edge_gate"
    schema: str = RESOLUTION_SCHEMA

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Resolution:
        """Reconstruct from a parsed JSON object (explicit, typed coercion)."""
        ds = d.get("deflated_sharpe")
        return Resolution(
            prereg_id=str(d["prereg_id"]),
            name=str(d["name"]),
            verdict=str(d["verdict"]),
            measured_n=int(d["measured_n"]),
            sample_size_target=int(d["sample_size_target"]),
            mean_net_bps=float(d["mean_net_bps"]),
            deflated_sharpe=(None if ds is None else float(ds)),
            ready=bool(d["ready"]),
            reason=str(d["reason"]),
            resolved_at_utc=str(d["resolved_at_utc"]),
            source=str(d.get("source", "canonical_edge_gate")),
            schema=str(d.get("schema", RESOLUTION_SCHEMA)),
        )


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.3f}"


def resolve_canonical(
    *,
    prereg_id: str,
    claim: dict[str, Any],
    verdict: EdgeValidationVerdict,
    resolved_at_utc: str,
) -> Resolution:
    """Map a measured edge-validation verdict onto the pre-committed claim.

    The canonical claim asserts ``net_mean_bps > 0 at n >= target, DSR >= conf``.
    Decision rule (monotone, direction-aware over a ``net>0`` claim):

    * ``MET`` — sample floor reached AND the gate is READY (net>0 AND DSR/MinTRL/
      outlier bars cleared): the pre-committed edge is statistically established.
    * ``NOT_MET`` — the claim is contradicted: the measured net mean is <= 0 (point
      estimate on the wrong side of a ``net>0`` claim → falsified regardless of n),
      OR the sample floor is reached but the statistical bar is not cleared.
    * ``INSUFFICIENT_N`` — net mean still positive but the sample is below the
      pre-committed target: neither confirmed nor falsified yet.
    """
    target = int(claim["sample_size_target"])
    name = str(claim.get("name", ""))
    n = verdict.trade_count
    mean = verdict.mean_net_bps
    dsr = verdict.deflated_sharpe

    if n >= target and verdict.ready:
        result = VERDICT_MET
        reason = (
            f"n={n} >= target={target} and all hard edge-validation criteria passed "
            f"(mean_net={mean:+.2f}bps, DSR={_fmt(dsr)})"
        )
    elif mean <= 0.0:
        result = VERDICT_NOT_MET
        reason = (
            f"measured net mean {mean:+.2f}bps <= 0 contradicts the pre-committed "
            f"'net_mean_bps>0' claim (n={n}, target={target}) — falsified"
        )
    elif n < target:
        result = VERDICT_INSUFFICIENT_N
        reason = (
            f"net mean {mean:+.2f}bps > 0 but n={n} < target={target}: "
            "under-sampled, neither confirmed nor falsified"
        )
    else:
        result = VERDICT_NOT_MET
        reason = (
            f"n={n} >= target={target} with net mean {mean:+.2f}bps > 0 but the "
            f"statistical bar was not cleared (DSR={_fmt(dsr)}) — not established"
        )

    return Resolution(
        prereg_id=prereg_id,
        name=name,
        verdict=result,
        measured_n=n,
        sample_size_target=target,
        mean_net_bps=round(mean, 4),
        deflated_sharpe=(None if dsr is None else round(dsr, 4)),
        ready=verdict.ready,
        reason=reason,
        resolved_at_utc=resolved_at_utc,
    )


def manual_resolution(
    *,
    prereg_id: str,
    name: str,
    verdict: str,
    note: str,
    resolved_at_utc: str,
) -> Resolution:
    """Operator-supplied resolution for a NON-canonical claim (no auto-measurement).

    The canonical edge is auto-measured; any other pre-registered hypothesis is
    resolved by hand — the operator states the verdict and a justifying note, and
    it is recorded with the same audit shape. ``verdict`` must be one of VERDICTS.
    """
    result = verdict.strip().upper()
    if result not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}, got {verdict!r}")
    return Resolution(
        prereg_id=prereg_id,
        name=name,
        verdict=result,
        measured_n=0,
        sample_size_target=0,
        mean_net_bps=0.0,
        deflated_sharpe=None,
        ready=False,
        reason=(note.strip() or "manual resolution (no note)"),
        resolved_at_utc=resolved_at_utc,
        source="manual",
    )


def render_resolution(res: Resolution) -> str:
    """Compact operator render of one resolution."""
    lines = [
        f"PREREG RESOLUTION: {res.verdict}  ({res.name or 'unnamed'})",
        f"  prereg_id: {res.prereg_id}",
        f"  measured_n: {res.measured_n}  target: {res.sample_size_target}",
        f"  mean_net_bps: {res.mean_net_bps:+.2f}  DSR: {_fmt(res.deflated_sharpe)}  "
        f"gate_ready: {res.ready}",
        f"  reason: {res.reason}",
        f"  resolved_at_utc: {res.resolved_at_utc}  source: {res.source}",
    ]
    return "\n".join(lines)


class ResolutionLedger:
    """Append-only JSONL ledger of pre-registration resolutions (verdicts)."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def record(self, res: Resolution) -> None:
        """Append one resolution (creates parent dirs on first write)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(res.to_json() + "\n")

    def entries(self) -> list[Resolution]:
        """All recorded resolutions (corrupt lines skipped, never raises)."""
        if not self._path.exists():
            return []
        out: list[Resolution] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, dict):
                    out.append(Resolution.from_dict(parsed))
            except (ValueError, TypeError, KeyError):
                continue  # a single bad line must never break a read
        return out

    def latest(self, prereg_id: str) -> Resolution | None:
        """Most recent resolution recorded for a prereg id (None if never)."""
        latest: Resolution | None = None
        for e in self.entries():
            if e.prereg_id == prereg_id:
                latest = e  # append-only → last wins
        return latest


__all__ = [
    "DEFAULT_PREREG_VERDICTS_PATH",
    "RESOLUTION_SCHEMA",
    "VERDICTS",
    "VERDICT_INSUFFICIENT_N",
    "VERDICT_MET",
    "VERDICT_NOT_MET",
    "Resolution",
    "ResolutionLedger",
    "manual_resolution",
    "render_resolution",
    "resolve_canonical",
]
