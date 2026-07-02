"""Source-lifecycle finite-state machine (pure logic).

Models the allowed transitions of a source through the lifecycle introduced by
the autonomous rotation/ranking system: ``planned -> probation -> active`` with
``silent``/``pinned``/``archived`` states. The engine
(``scripts/source_lifecycle_recalc.py``) drives transitions; this module only
VALIDATES them, so a structurally impossible jump fails loudly. No I/O, no DB.

Engine-layer invariants (replace-only-when-ready archival, evidence-backed
pin/demote) are intentionally NOT encoded here — the FSM rejects only
impossible jumps, not policy.
"""

from __future__ import annotations

from app.core.enums import SourceStatus

# RETIRED (2026-06-29): operator-manual TERMINAL kill. Reachable from every
# lifecycle state (an operator can retire a source from anywhere), but has NO
# outgoing edges → no auto-resurrection and, combined with the status-blind
# onboarding dedup, no re-onboarding. Makes "never again" a hard FSM guarantee
# instead of relying on rotation-policy conservatism.
_TRANSITIONS: dict[SourceStatus, frozenset[SourceStatus]] = {
    SourceStatus.PLANNED: frozenset(
        {SourceStatus.PROBATION, SourceStatus.DISABLED, SourceStatus.RETIRED}
    ),
    SourceStatus.PROBATION: frozenset(
        {SourceStatus.ACTIVE, SourceStatus.ARCHIVED, SourceStatus.DISABLED, SourceStatus.RETIRED}
    ),
    SourceStatus.ACTIVE: frozenset(
        {
            SourceStatus.SILENT,
            SourceStatus.PINNED,
            SourceStatus.ARCHIVED,
            SourceStatus.DISABLED,
            SourceStatus.RETIRED,
        }
    ),
    SourceStatus.SILENT: frozenset(
        {SourceStatus.ACTIVE, SourceStatus.ARCHIVED, SourceStatus.DISABLED, SourceStatus.RETIRED}
    ),
    SourceStatus.PINNED: frozenset(
        {SourceStatus.ACTIVE, SourceStatus.DISABLED, SourceStatus.RETIRED}
    ),
    SourceStatus.ARCHIVED: frozenset(
        {SourceStatus.PROBATION, SourceStatus.RETIRED}
    ),  # re-evaluation OR terminal kill
    SourceStatus.DISABLED: frozenset(
        {SourceStatus.PROBATION, SourceStatus.ACTIVE, SourceStatus.RETIRED}
    ),
    SourceStatus.RETIRED: frozenset(),  # TERMINAL — no return
}

LIFECYCLE_STATES: frozenset[SourceStatus] = frozenset(_TRANSITIONS) | {
    target for targets in _TRANSITIONS.values() for target in targets
}


def can_transition(current: SourceStatus, target: SourceStatus) -> bool:
    """True iff ``current -> target`` is a structurally allowed lifecycle move.

    An idempotent no-op (``current == target``) is always allowed.
    """
    if current == target:
        return True
    return target in _TRANSITIONS.get(current, frozenset())


def next_status(current: SourceStatus, target: SourceStatus) -> SourceStatus:
    """Return ``target`` if the transition is allowed, else raise ``ValueError``."""
    if not can_transition(current, target):
        raise ValueError(f"illegal source lifecycle transition: {current.value} -> {target.value}")
    return target
