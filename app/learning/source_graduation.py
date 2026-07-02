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

Two eligibility paths feed the same replace-only-when-ready machinery:

* **Directional** (default) — the historical path: a candidate with enough resolved
  directional outcomes (``deliveries``) replaces the weakest rotation source it is
  *strictly better* than on the Wilson axis.
* **Delivery-reclamation** (``allow_delivery_reclamation``, operator-gated, default
  off) — a context/news source emits narrative, not resolvable directional signals,
  so its ``deliveries`` is ~0 and the directional path never opens for it. Instead, a
  source that *sustains document delivery* (a boolean floor computed upstream — the
  engine here sees only ``delivering``, never document volume, so volume cannot be
  gamed into a score) may reclaim a slot held by a **SILENT** (non-delivering) active
  source. The comparison is single-axis and trivially monotone: *delivering replaces
  non-delivering*. It NEVER touches a still-signalling directional source (that would
  trade a measured edge-bearer for an unmeasured one), and it feeds NO trust/priority
  modifier — so it manufactures no edge claim (ADR 0012). ``ACTIVE`` means curated/
  rotatable, NOT validated/trusted.
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
    delivering: bool = False  # sustained document delivery (boolean floor, not volume)


@dataclass(frozen=True)
class RotationCandidate:
    """An active source flagged for rotation, with its current score."""

    source: str
    score: float
    silent: bool = False  # delivering NEITHER signals NOR documents → reclaimable


@dataclass(frozen=True)
class GraduationSwap:
    """One atomic replace-only-when-ready pair: promote ``into``, archive ``out``."""

    promote: str
    archive: str
    promote_score: float
    archive_score: float
    kind: str = "directional"  # "directional" | "delivery_reclamation"


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
    allow_delivery_reclamation: bool = False,
) -> GraduationPlan:
    """Pair proven probation candidates with replaceable rotation sources.

    Greedy + deterministic over two ordered phases that share one ``consumed``
    target set, so the active set never grows (1-in-1-out) regardless of path. The
    **delivery phase runs first** because it is the constrained side (silent-only
    targets); directional candidates are flexible and fill whatever remains:

    1. **Delivery-reclamation** (only when ``allow_delivery_reclamation``) — a
       candidate that is ``delivering`` but directionally thin reclaims a slot held by
       a **SILENT** active source. Single-axis and monotone (delivering replaces
       non-delivering): no Wilson comparison, no document-volume score, and silent-only
       targets so a still-signalling source is never traded.
    2. **Directional** — the best eligible candidate (``deliveries >= min_deliveries``)
       replaces the weakest *still-free* rotation source it scores *strictly higher*
       than. A candidate with no strictly-weaker partner left is skipped — the active
       set is never expanded.
    """
    directional_eligible: list[ProbationCandidate] = []
    delivery_eligible: list[ProbationCandidate] = []
    skipped: list[tuple[str, str]] = []
    for c in probation_candidates:
        if c.runs < min_probation_runs:
            skipped.append((c.source, f"probation_runs<{min_probation_runs}"))
        elif c.deliveries >= min_deliveries:
            directional_eligible.append(c)
        elif allow_delivery_reclamation and c.delivering:
            delivery_eligible.append(c)
        else:
            skipped.append((c.source, f"deliveries<{min_deliveries}"))

    consumed: set[str] = set()
    swaps: list[GraduationSwap] = []
    weakest_first = sorted(rotation_pool, key=lambda r: (r.score, r.source))

    # Phase 1 — delivery-reclamation: each delivering candidate takes one SILENT slot.
    # Constrained side first (silent-only targets), so directional cannot starve it.
    silent_targets = [r for r in weakest_first if r.silent]
    delivery_eligible.sort(key=lambda c: c.source)
    for i, cand in enumerate(delivery_eligible):
        if i >= len(silent_targets):
            skipped.append((cand.source, "no_silent_rotation_target"))
            continue
        slot = silent_targets[i]
        consumed.add(slot.source)
        swaps.append(
            GraduationSwap(
                promote=cand.source,
                archive=slot.source,
                promote_score=cand.score,
                archive_score=slot.score,
                kind="delivery_reclamation",
            )
        )

    # Phase 2 — directional: best candidate first; weakest still-free target first.
    directional_eligible.sort(key=lambda c: (-c.score, c.source))
    for cand in directional_eligible:
        target = next((r for r in weakest_first if r.source not in consumed), None)
        if target is None:
            skipped.append((cand.source, "no_rotation_target"))
            continue
        if cand.score <= target.score:
            # Not strictly better than even the weakest target → do not graduate.
            skipped.append((cand.source, "not_strictly_better_than_weakest_rotation"))
            continue
        consumed.add(target.source)
        swaps.append(
            GraduationSwap(
                promote=cand.source,
                archive=target.source,
                promote_score=cand.score,
                archive_score=target.score,
                kind="directional",
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
