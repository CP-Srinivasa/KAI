"""Source-lifecycle FSM tests (Phase 1 of the source-lifecycle plan)."""

from __future__ import annotations

import pytest

from app.core.enums import SourceStatus
from app.learning.source_lifecycle import can_transition, next_status


def test_onboarding_path_planned_to_active() -> None:
    assert can_transition(SourceStatus.PLANNED, SourceStatus.PROBATION)
    assert can_transition(SourceStatus.PROBATION, SourceStatus.ACTIVE)
    assert next_status(SourceStatus.PROBATION, SourceStatus.ACTIVE) == SourceStatus.ACTIVE


def test_silent_and_recovery() -> None:
    assert can_transition(SourceStatus.ACTIVE, SourceStatus.SILENT)
    assert can_transition(SourceStatus.SILENT, SourceStatus.ACTIVE)


def test_pin_and_evidence_backed_demote() -> None:
    assert can_transition(SourceStatus.ACTIVE, SourceStatus.PINNED)
    assert can_transition(SourceStatus.PINNED, SourceStatus.ACTIVE)
    # A pinned source must NOT jump straight to archived — it demotes first.
    assert not can_transition(SourceStatus.PINNED, SourceStatus.ARCHIVED)


def test_archive_reachable_from_active_silent_probation() -> None:
    assert can_transition(SourceStatus.ACTIVE, SourceStatus.ARCHIVED)
    assert can_transition(SourceStatus.SILENT, SourceStatus.ARCHIVED)
    assert can_transition(SourceStatus.PROBATION, SourceStatus.ARCHIVED)
    assert can_transition(SourceStatus.ARCHIVED, SourceStatus.PROBATION)  # re-evaluation


def test_planned_cannot_skip_probation() -> None:
    assert not can_transition(SourceStatus.PLANNED, SourceStatus.ACTIVE)
    with pytest.raises(ValueError):
        next_status(SourceStatus.PLANNED, SourceStatus.ACTIVE)


def test_idempotent_no_op_allowed() -> None:
    assert can_transition(SourceStatus.ACTIVE, SourceStatus.ACTIVE)
    assert next_status(SourceStatus.ACTIVE, SourceStatus.ACTIVE) == SourceStatus.ACTIVE
