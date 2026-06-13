"""Generator-Edge side-channel collector (Issue #170 Part B, 2026-06-11).

#161 built the measuring instrument (``generator_edge``) and its CLI honestly
reports IC-by-horizon and Brier/ECE as ``None`` "until a feeder supplies them".
This module is that feeder: it reads the SHADOW resolution stream
(``artifacts/shadow_candidate_resolved.jsonl`` — written by the resolver since
the S4 wiring made the REAL generator measurable) and derives the two
side-channel inputs ``build_generator_edge_report`` accepts:

  - ``ic_aligned_by_cohort``  — cohort → horizon-label → [(signal_score,
    side-adjusted forward return bps)] for the Information Coefficient.
  - ``outcome_pairs_by_cohort`` — cohort → [OutcomePair(predicted_probability=
    signal_confidence, actual_outcome=0/1)] for Brier/ECE.

Honesty contracts:
  - REAL only: rows whose ``source`` is not in ``real_sources`` or that carry
    ``is_canary=True`` are excluded and counted — canary probes must never
    poison the edge evidence again (incident class 2026-06-03).
  - Outcome definition is conservative and auditable: take-hit without
    stop-hit → 1; stop-hit without take-hit → 0; both touched → whichever came
    first (``mfe_before_mae``); neither → sign of the 1h forward return; a
    flat/missing 1h return yields NO pair (counted as skipped, never invented).
  - Horizons beyond the ledger (4h/24h) stay absent → the instrument reports
    IC=None for them, exactly as designed.

Read-only; pure file → dataclass transformation.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.learning.calibration import OutcomePair

logger = logging.getLogger(__name__)

DEFAULT_RESOLVED_PATH = Path("artifacts/shadow_candidate_resolved.jsonl")

# Resolver horizon fields → generator_edge IC horizon labels.
HORIZON_FIELDS: tuple[tuple[str, str], ...] = (
    ("1m", "fwd_60s_bps"),
    ("5m", "fwd_300s_bps"),
    ("15m", "fwd_900s_bps"),
    ("1h", "fwd_3600s_bps"),
)

# The fail-closed REAL set (mirrors the shadow-report REAL_SOURCES contract).
REAL_SOURCES: tuple[str, ...] = ("autonomous_generator",)


@dataclass(frozen=True)
class CollectedEdgeInputs:
    """Side-channel inputs for ``build_generator_edge_report`` + audit counters."""

    ic_aligned_by_cohort: dict[str, dict[str, list[tuple[float, float]]]] = field(
        default_factory=dict
    )
    outcome_pairs_by_cohort: dict[str, list[OutcomePair]] = field(default_factory=dict)
    resolved_real: int = 0
    skipped_non_real: int = 0
    skipped_canary: int = 0
    skipped_no_score: int = 0
    skipped_undecidable_outcome: int = 0

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "resolved_real": self.resolved_real,
            "skipped_non_real": self.skipped_non_real,
            "skipped_canary": self.skipped_canary,
            "skipped_no_score": self.skipped_no_score,
            "skipped_undecidable_outcome": self.skipped_undecidable_outcome,
            "cohorts": sorted(self.ic_aligned_by_cohort),
        }


def _cohort_key(row: dict[str, Any], cohort_type: str) -> str:
    if cohort_type == "regime":
        return str(row.get("regime") or "unknown")
    if cohort_type == "symbol":
        return str(row.get("symbol") or "unknown")
    # NOT folded here (2026-06-13): the shadow resolver only ever emits
    # ``autonomous_generator`` (``real_analysis`` is an audit-fill tag, never a
    # shadow-candidate tag), so folding would be a no-op for real data — and this
    # key is shared with the watchdog agent-scoreboard, which intentionally
    # scores any future real_analysis shadow row as its own agent. The
    # cohort-mismatch fold lives only in the edge report's trade-side _key.
    return str(row.get("source") or "unknown")


def _resolve_outcome(row: dict[str, Any]) -> int | None:
    """Binary outcome, conservative + auditable (see module docstring)."""
    reached_take = bool(row.get("reached_take"))
    reached_stop = bool(row.get("reached_stop"))
    if reached_take and not reached_stop:
        return 1
    if reached_stop and not reached_take:
        return 0
    if reached_take and reached_stop:
        return 1 if bool(row.get("mfe_before_mae")) else 0
    fwd = row.get("fwd_3600s_bps")
    if isinstance(fwd, (int, float)) and fwd != 0:
        return 1 if fwd > 0 else 0
    return None


def collect_edge_inputs_from_resolved(
    resolved_path: Path = DEFAULT_RESOLVED_PATH,
    *,
    cohort_type: str = "generator",
    real_sources: tuple[str, ...] = REAL_SOURCES,
) -> CollectedEdgeInputs:
    """Collect IC alignment + calibration pairs from the resolved shadow ledger.

    A missing/unreadable file yields empty inputs (the instrument then reports
    IC/Brier as ``None`` — honest absence, never fabricated numbers).
    """
    ic: dict[str, dict[str, list[tuple[float, float]]]] = {}
    pairs: dict[str, list[OutcomePair]] = {}
    resolved_real = 0
    skipped_non_real = 0
    skipped_canary = 0
    skipped_no_score = 0
    skipped_undecidable = 0

    if not resolved_path.exists():
        return CollectedEdgeInputs()

    try:
        with resolved_path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(row, dict):
                    continue

                if row.get("is_canary"):
                    skipped_canary += 1
                    continue
                if str(row.get("source")) not in real_sources:
                    skipped_non_real += 1
                    continue
                score = row.get("signal_confidence")
                if not isinstance(score, (int, float)):
                    skipped_no_score += 1
                    continue

                resolved_real += 1
                cohort = _cohort_key(row, cohort_type)

                cohort_ic = ic.setdefault(cohort, {})
                for label, fld in HORIZON_FIELDS:
                    fwd = row.get(fld)
                    if isinstance(fwd, (int, float)):
                        cohort_ic.setdefault(label, []).append((float(score), float(fwd)))

                outcome = _resolve_outcome(row)
                if outcome is None:
                    skipped_undecidable += 1
                    continue
                pairs.setdefault(cohort, []).append(
                    OutcomePair(
                        decision_id=str(row.get("candidate_id") or "unknown"),
                        predicted_probability=float(min(max(score, 0.0), 1.0)),
                        actual_outcome=outcome,
                        regime=row.get("regime"),
                    )
                )
    except OSError as exc:
        logger.warning("[edge-collector] read failed %s: %s", resolved_path, exc)
        return CollectedEdgeInputs()

    return CollectedEdgeInputs(
        ic_aligned_by_cohort=ic,
        outcome_pairs_by_cohort=pairs,
        resolved_real=resolved_real,
        skipped_non_real=skipped_non_real,
        skipped_canary=skipped_canary,
        skipped_no_score=skipped_no_score,
        skipped_undecidable_outcome=skipped_undecidable,
    )


__all__ = [
    "DEFAULT_RESOLVED_PATH",
    "HORIZON_FIELDS",
    "REAL_SOURCES",
    "CollectedEdgeInputs",
    "collect_edge_inputs_from_resolved",
]
