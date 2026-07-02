"""Asset-lifecycle finite-state machine (pure logic, no I/O).

G1 of the Momentum-Universe goal. Models the allowed transitions of a traded
asset through its rotation lifecycle — ``candidate -> probation -> active`` with
``rotation_flagged`` (sustained-weak warning), ``pinned`` (never auto-rotated),
and ``archived`` (rotated out, re-evaluatable). Mirrors ``source_lifecycle``: the
engine drives transitions, this module only VALIDATES them so a structurally
impossible jump fails loudly. Policy (hysteresis, replace-only-when-ready) lives
in ``asset_rotation_policy``, not here.
"""

from __future__ import annotations

from enum import StrEnum


class AssetStatus(StrEnum):
    CANDIDATE = "candidate"
    PROBATION = "probation"
    ACTIVE = "active"
    ROTATION_FLAGGED = "rotation_flagged"
    PINNED = "pinned"
    ARCHIVED = "archived"


_TRANSITIONS: dict[AssetStatus, frozenset[AssetStatus]] = {
    AssetStatus.CANDIDATE: frozenset({AssetStatus.PROBATION, AssetStatus.ARCHIVED}),
    AssetStatus.PROBATION: frozenset(
        {AssetStatus.ACTIVE, AssetStatus.ROTATION_FLAGGED, AssetStatus.ARCHIVED}
    ),
    AssetStatus.ACTIVE: frozenset(
        {AssetStatus.ROTATION_FLAGGED, AssetStatus.PINNED, AssetStatus.ARCHIVED}
    ),
    AssetStatus.ROTATION_FLAGGED: frozenset({AssetStatus.ACTIVE, AssetStatus.ARCHIVED}),
    AssetStatus.PINNED: frozenset({AssetStatus.ACTIVE}),
    AssetStatus.ARCHIVED: frozenset({AssetStatus.CANDIDATE}),  # re-evaluation
}

LIFECYCLE_STATES: frozenset[AssetStatus] = frozenset(_TRANSITIONS) | {
    target for targets in _TRANSITIONS.values() for target in targets
}


def can_transition(current: AssetStatus, target: AssetStatus) -> bool:
    """True iff ``current -> target`` is a structurally allowed move.

    An idempotent no-op (``current == target``) is always allowed.
    """
    if current == target:
        return True
    return target in _TRANSITIONS.get(current, frozenset())


def next_status(current: AssetStatus, target: AssetStatus) -> AssetStatus:
    """Return ``target`` if the transition is allowed, else raise ``ValueError``."""
    if not can_transition(current, target):
        raise ValueError(f"illegal asset lifecycle transition: {current.value} -> {target.value}")
    return target
