"""Replace-only-when-ready graduation decision (pure logic).

Phase 3 safety core (PR5). When a probation candidate has proven itself, it may
only enter the active set by REPLACING a strictly-weaker active source that is
already flagged for rotation — never by expanding the set, and never by archiving
a source without a ready, better replacement (Rail 8, ADR-0006).

This module is pure: it decides the swaps; the scheduler executes them (DB +
audit) only when the discovery kill-switch is on. The guarantees encoded here
hold in BOTH dry and live mode, because they are computed here, not at execution:

1. **1-in-1-out** — every graduation is paired with exactly one archival, so the
   active-set size never grows from autonomous discovery.
2. **Strictly-better** — a candidate replaces a rotation source only if its score
   is strictly greater; a tie or worse candidate is not graduated.
3. **Evidence gate** — a candidate must meet the probation window (runs) AND a
   minimum delivery count before it is even eligible.
4. **No orphan archival** — a rotation source is archived ONLY as the partner of a
   graduation; an unmatched rotation source stays active (replace-only-when-ready).
"""

from __future__ import annotations

from dataclasses import dataclass

# Default evidence gates for graduation. Conservative: a candidate must survive a
# few probation runs and actually deliver before it can displace an active source.
#
# KNOWN-STRUCTURALLY-CLOSED (ADR 0012, 2026-06-29): ``deliveries`` is the count of
# resolved-DIRECTION outcomes a source produced on probation, which for news/RSS
# sources is ~0 (they emit narrative, not resolvable directional signals). So this
# gate effectively never opens for the dominant source class — graduation stays
# inert. This is left UNCHANGED on purpose: under the NORTH_STAR pivot KAI is a
# truth/falsification platform, not an alpha-bot, so we do NOT bend the delivery
# attribution to force auto-promotion (that would manufacture an edge claim from a
# source class we have not validated). When/if a delivery signal is genuinely
# earned, raise it through the evidence path, not by lowering this gate.
DEFAULT_MIN_PROBATION_RUNS: int = 3
DEFAULT_MIN_DELIVERIES: int = 5


@dataclass(frozen=True)
class ProbationCandidate:
    """A source currently in probation, with its accumulated evidence."""

    source: str
    score: float  # comparable quality score (e.g. Wilson-Lower of delivered signals)
    deliveries: int  # how many usable items it produced while on probation
    runs: int  # how many probation evaluation cycles it has survived


@dataclass(frozen=True)
class RotationCandidate:
    """An active source flagged for rotation, with its current score."""

    source: str
    score: float


@dataclass(frozen=True)
class GraduationSwap:
    """One atomic replace-only-when-ready pair: promote ``into``, archive ``out``."""

    promote: str
    archive: str
    promote_score: float
    archive_score: float


@dataclass(frozen=True)
class GraduationPlan:
    """The decided swaps plus the candidates that were considered but not graduated."""

    swaps: list[GraduationSwap]
    skipped: list[tuple[str, str]]  # (source, reason)


def decide_graduation(
    probation_candidates: list[ProbationCandidate],
    rotation_pool: list[RotationCandidate],
    *,
    min_probation_runs: int = DEFAULT_MIN_PROBATION_RUNS,
    min_deliveries: int = DEFAULT_MIN_DELIVERIES,
) -> GraduationPlan:
    """Pair proven probation candidates with strictly-weaker rotation sources.

    Greedy + deterministic: the best eligible candidate replaces the weakest
    rotation source, provided it scores strictly higher. Each rotation source is
    consumed at most once. A candidate with no weaker rotation partner left is
    skipped (``no_weaker_rotation_target``) — the active set is never expanded.
    """
    eligible: list[ProbationCandidate] = []
    skipped: list[tuple[str, str]] = []
    for c in probation_candidates:
        if c.runs < min_probation_runs:
            skipped.append((c.source, f"probation_runs<{min_probation_runs}"))
        elif c.deliveries < min_deliveries:
            skipped.append((c.source, f"deliveries<{min_deliveries}"))
        else:
            eligible.append(c)

    # Best candidate first; weakest rotation target first.
    eligible.sort(key=lambda c: (-c.score, c.source))
    remaining = sorted(rotation_pool, key=lambda r: (r.score, r.source))

    swaps: list[GraduationSwap] = []
    for cand in eligible:
        # The weakest still-available rotation target.
        target = remaining[0] if remaining else None
        if target is None:
            skipped.append((cand.source, "no_rotation_target"))
            continue
        if cand.score <= target.score:
            # Not strictly better than even the weakest target → do not graduate.
            skipped.append((cand.source, "not_strictly_better_than_weakest_rotation"))
            continue
        remaining.pop(0)
        swaps.append(
            GraduationSwap(
                promote=cand.source,
                archive=target.source,
                promote_score=cand.score,
                archive_score=target.score,
            )
        )
    return GraduationPlan(swaps=swaps, skipped=skipped)


__all__ = [
    "DEFAULT_MIN_DELIVERIES",
    "DEFAULT_MIN_PROBATION_RUNS",
    "GraduationPlan",
    "GraduationSwap",
    "ProbationCandidate",
    "RotationCandidate",
    "decide_graduation",
]
