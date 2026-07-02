"""Unit tests for the operator-approval service."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.learning.approval import (
    STATUS_ACTIVE,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_SUPERSEDED,
    ApprovalService,
)
from app.learning.parameter_version import ParameterVersionStore


@pytest.fixture
def service(tmp_path: Path) -> ApprovalService:
    return ApprovalService(ParameterVersionStore(tmp_path / "journal.jsonl"))


# ============================================================================
# Status calculation
# ============================================================================


def test_pending_status_for_fresh_proposal(service: ApprovalService):
    proposal = service.store.propose_version(
        parameter_path="bayes.calibrator.global",
        parameter_set={"intercept": 0.05, "slope": 0.92},
    )
    status = service.get_status(proposal.version_id)
    assert status is not None
    assert status.status == STATUS_PENDING
    assert status.activated_at_utc is None
    assert status.rejected_at_utc is None
    assert status.superseded_by is None


def test_active_status_after_approve(service: ApprovalService):
    proposal = service.store.propose_version(parameter_path="p", parameter_set={"v": 1})
    service.approve(
        parameter_path="p",
        version_id=proposal.version_id,
        operator_id="sascha",
    )
    status = service.get_status(proposal.version_id)
    assert status.status == STATUS_ACTIVE
    assert status.activated_at_utc is not None


def test_rejected_status_after_reject(service: ApprovalService):
    proposal = service.store.propose_version(parameter_path="p", parameter_set={"v": 1})
    service.reject(
        parameter_path="p",
        version_id=proposal.version_id,
        operator_id="sascha",
        reason="OoS performance regression",
    )
    status = service.get_status(proposal.version_id)
    assert status.status == STATUS_REJECTED
    assert status.rejected_at_utc is not None


def test_superseded_when_a_later_proposal_takes_over(service: ApprovalService):
    p1 = service.store.propose_version(parameter_path="p", parameter_set={"v": 1})
    p2 = service.store.propose_version(parameter_path="p", parameter_set={"v": 2})
    service.approve(parameter_path="p", version_id=p1.version_id, operator_id="op1")
    service.approve(parameter_path="p", version_id=p2.version_id, operator_id="op1")
    s1 = service.get_status(p1.version_id)
    s2 = service.get_status(p2.version_id)
    assert s1.status == STATUS_SUPERSEDED
    assert s1.superseded_by == p2.version_id
    assert s2.status == STATUS_ACTIVE


def test_unknown_version_returns_none(service: ApprovalService):
    assert service.get_status("pv_does_not_exist") is None


# ============================================================================
# Listing
# ============================================================================


def test_list_proposals_filters_by_path(service: ApprovalService):
    service.store.propose_version(parameter_path="a", parameter_set={"v": 1})
    service.store.propose_version(parameter_path="a", parameter_set={"v": 2})
    service.store.propose_version(parameter_path="b", parameter_set={"v": 3})
    assert len(service.list_proposals()) == 3
    assert len(service.list_proposals(parameter_path="a")) == 2
    assert len(service.list_proposals(parameter_path="b")) == 1


def test_list_pending_excludes_active_rejected_superseded(service: ApprovalService):
    p1 = service.store.propose_version(parameter_path="p", parameter_set={"v": 1})
    p2 = service.store.propose_version(parameter_path="p", parameter_set={"v": 2})
    p3 = service.store.propose_version(parameter_path="p", parameter_set={"v": 3})
    service.approve(parameter_path="p", version_id=p1.version_id, operator_id="op")
    service.approve(parameter_path="p", version_id=p2.version_id, operator_id="op")
    # p1 is now superseded, p2 is active, p3 is still pending
    pending = service.list_pending()
    assert len(pending) == 1
    assert pending[0].proposal.version_id == p3.version_id


# ============================================================================
# Approval rules
# ============================================================================


def test_approve_requires_operator_id(service: ApprovalService):
    proposal = service.store.propose_version(parameter_path="p", parameter_set={"v": 1})
    with pytest.raises(ValueError, match="operator_id_required"):
        service.approve(parameter_path="p", version_id=proposal.version_id, operator_id="")
    with pytest.raises(ValueError, match="operator_id_required"):
        service.approve(parameter_path="p", version_id=proposal.version_id, operator_id="   ")


def test_approve_unknown_version_raises(service: ApprovalService):
    with pytest.raises(ValueError, match="unknown_proposal"):
        service.approve(parameter_path="p", version_id="pv_unknown", operator_id="op")


def test_approve_rejected_proposal_raises(service: ApprovalService):
    proposal = service.store.propose_version(parameter_path="p", parameter_set={"v": 1})
    service.reject(
        parameter_path="p",
        version_id=proposal.version_id,
        operator_id="op",
        reason="bad fit",
    )
    with pytest.raises(ValueError, match="already_rejected"):
        service.approve(parameter_path="p", version_id=proposal.version_id, operator_id="op")


def test_approve_already_active_raises(service: ApprovalService):
    proposal = service.store.propose_version(parameter_path="p", parameter_set={"v": 1})
    service.approve(parameter_path="p", version_id=proposal.version_id, operator_id="op")
    with pytest.raises(ValueError, match="already_active"):
        service.approve(parameter_path="p", version_id=proposal.version_id, operator_id="op")


def test_approve_superseded_raises_with_rollback_hint(service: ApprovalService):
    p1 = service.store.propose_version(parameter_path="p", parameter_set={"v": 1})
    p2 = service.store.propose_version(parameter_path="p", parameter_set={"v": 2})
    service.approve(parameter_path="p", version_id=p1.version_id, operator_id="op")
    service.approve(parameter_path="p", version_id=p2.version_id, operator_id="op")
    with pytest.raises(ValueError, match="rollback"):
        service.approve(parameter_path="p", version_id=p1.version_id, operator_id="op")


# ============================================================================
# Reject rules
# ============================================================================


def test_reject_requires_reason(service: ApprovalService):
    proposal = service.store.propose_version(parameter_path="p", parameter_set={"v": 1})
    with pytest.raises(ValueError, match="reason_required"):
        service.reject(
            parameter_path="p",
            version_id=proposal.version_id,
            operator_id="op",
            reason="",
        )
    with pytest.raises(ValueError, match="reason_required"):
        service.reject(
            parameter_path="p",
            version_id=proposal.version_id,
            operator_id="op",
            reason="   ",
        )


def test_reject_active_version_raises(service: ApprovalService):
    proposal = service.store.propose_version(parameter_path="p", parameter_set={"v": 1})
    service.approve(parameter_path="p", version_id=proposal.version_id, operator_id="op")
    with pytest.raises(ValueError, match="cannot_reject_active"):
        service.reject(
            parameter_path="p",
            version_id=proposal.version_id,
            operator_id="op",
            reason="bad in retrospect",
        )


def test_reject_already_rejected_raises(service: ApprovalService):
    proposal = service.store.propose_version(parameter_path="p", parameter_set={"v": 1})
    service.reject(
        parameter_path="p",
        version_id=proposal.version_id,
        operator_id="op",
        reason="first reason",
    )
    with pytest.raises(ValueError, match="already_rejected"):
        service.reject(
            parameter_path="p",
            version_id=proposal.version_id,
            operator_id="op",
            reason="second reason",
        )


# ============================================================================
# Rollback rules
# ============================================================================


def test_rollback_to_earlier_version_replaces_active(service: ApprovalService):
    p1 = service.store.propose_version(parameter_path="p", parameter_set={"v": 1})
    p2 = service.store.propose_version(parameter_path="p", parameter_set={"v": 2})
    service.approve(parameter_path="p", version_id=p1.version_id, operator_id="op")
    service.approve(parameter_path="p", version_id=p2.version_id, operator_id="op")
    service.rollback(
        parameter_path="p",
        version_id=p1.version_id,
        operator_id="op",
        notes="regression in P&L",
    )
    s1 = service.get_status(p1.version_id)
    s2 = service.get_status(p2.version_id)
    assert s1.status == STATUS_ACTIVE
    assert s2.status == STATUS_SUPERSEDED


def test_rollback_to_active_version_is_rejected(service: ApprovalService):
    proposal = service.store.propose_version(parameter_path="p", parameter_set={"v": 1})
    service.approve(parameter_path="p", version_id=proposal.version_id, operator_id="op")
    with pytest.raises(ValueError, match="already_active_no_op"):
        service.rollback(
            parameter_path="p",
            version_id=proposal.version_id,
            operator_id="op",
            notes="meaningless",
        )


def test_rollback_to_rejected_is_refused(service: ApprovalService):
    proposal = service.store.propose_version(parameter_path="p", parameter_set={"v": 1})
    service.reject(
        parameter_path="p",
        version_id=proposal.version_id,
        operator_id="op",
        reason="bad fit",
    )
    with pytest.raises(ValueError, match="cannot_rollback_to_rejected"):
        service.rollback(
            parameter_path="p",
            version_id=proposal.version_id,
            operator_id="op",
            notes="changed my mind",
        )


def test_rollback_requires_notes(service: ApprovalService):
    p1 = service.store.propose_version(parameter_path="p", parameter_set={"v": 1})
    p2 = service.store.propose_version(parameter_path="p", parameter_set={"v": 2})
    service.approve(parameter_path="p", version_id=p1.version_id, operator_id="op")
    service.approve(parameter_path="p", version_id=p2.version_id, operator_id="op")
    with pytest.raises(ValueError, match="notes_required_for_rollback"):
        service.rollback(
            parameter_path="p",
            version_id=p1.version_id,
            operator_id="op",
            notes="",
        )


# ============================================================================
# Audit chain integrity preserved by service writes
# ============================================================================


def test_chain_remains_valid_after_full_lifecycle(service: ApprovalService):
    p1 = service.store.propose_version(parameter_path="p", parameter_set={"v": 1})
    p2 = service.store.propose_version(parameter_path="p", parameter_set={"v": 2})
    p3 = service.store.propose_version(parameter_path="p", parameter_set={"v": 3})
    service.approve(parameter_path="p", version_id=p1.version_id, operator_id="op")
    service.approve(parameter_path="p", version_id=p2.version_id, operator_id="op")
    service.reject(
        parameter_path="p",
        version_id=p3.version_id,
        operator_id="op",
        reason="not enough samples",
    )
    service.rollback(
        parameter_path="p",
        version_id=p1.version_id,
        operator_id="op",
        notes="undo experiment",
    )
    ok, err = service.verify_chain()
    assert ok, err


def test_operator_id_persisted_through_approve(service: ApprovalService):
    proposal = service.store.propose_version(parameter_path="p", parameter_set={"v": 1})
    activation = service.approve(
        parameter_path="p",
        version_id=proposal.version_id,
        operator_id="sascha",
        notes="walk-forward + counterfactual both approve",
    )
    assert activation.created_by == "sascha"
    assert activation.notes == "walk-forward + counterfactual both approve"


def test_history_returns_records_in_order(service: ApprovalService):
    p1 = service.store.propose_version(parameter_path="p", parameter_set={"v": 1})
    service.approve(parameter_path="p", version_id=p1.version_id, operator_id="op")
    service.store.propose_version(parameter_path="p", parameter_set={"v": 2})
    history = service.history("p")
    assert len(history) == 3
    assert [r.record_type for r in history] == [
        "version_proposed",
        "version_activated",
        "version_proposed",
    ]
