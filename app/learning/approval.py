"""Operator-Approval-Service auf dem ParameterVersionStore.

Dünner Service-Layer, der den raw Hash-chained Store um drei Dinge anreichert:

1. **Status-Berechnung** pro Vorschlag — wie aus den Folge-Events
   (activate/reject/rollback) der aktuelle Lebenslauf eines Vorschlags
   abzuleiten ist.
2. **Audit-Felder** — `created_by` wird zwingend gefordert (kein silent
   "auto" mehr); ein Approval ohne Operator-ID wird abgewiesen.
3. **Convenience-Lookups** für CLI/Reports.

Der Store selbst bleibt low-level (er kennt keine Operator-Politik).
Diese Schicht ist die Naht zwischen low-level Audit und Operator-UX.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from app.learning.config_snapshot import write_snapshot
from app.learning.parameter_version import (
    ParameterChange,
    ParameterVersionStore,
)

ProposalStatusLiteral = Literal["pending", "active", "superseded", "rejected"]

STATUS_PENDING: Final[ProposalStatusLiteral] = "pending"
STATUS_ACTIVE: Final[ProposalStatusLiteral] = "active"
STATUS_SUPERSEDED: Final[ProposalStatusLiteral] = "superseded"
STATUS_REJECTED: Final[ProposalStatusLiteral] = "rejected"


class ProposalStatus(BaseModel):
    """Status-Snapshot eines einzelnen Vorschlags."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal: ParameterChange
    status: ProposalStatusLiteral
    activated_at_utc: str | None  # timestamp of last activate/rollback referencing it
    rejected_at_utc: str | None
    superseded_by: str | None  # version_id of the proposal that took over


# ─── Status calculation ───────────────────────────────────────────────────────


def _proposal_status_from_records(
    records: list[ParameterChange], proposal: ParameterChange
) -> ProposalStatus:
    """Walk all journal records and derive status of `proposal`.

    Records are assumed to be in chronological (= file) order.
    """
    if proposal.record_type != "version_proposed":
        # defensive — caller should only pass proposals
        return ProposalStatus(
            proposal=proposal,
            status=STATUS_PENDING,
            activated_at_utc=None,
            rejected_at_utc=None,
            superseded_by=None,
        )

    activated_at: str | None = None
    rejected_at: str | None = None
    latest_active_event: ParameterChange | None = None

    for r in records:
        if r.parameter_path != proposal.parameter_path:
            continue
        if r.record_type == "version_rejected" and r.version_id == proposal.version_id:
            rejected_at = r.timestamp_utc
        elif r.record_type in ("version_activated", "version_rolled_back"):
            latest_active_event = r
            if r.version_id == proposal.version_id:
                activated_at = r.timestamp_utc

    # Order matters: rejected wins over everything else if it ever happened.
    # (Operator can only reject a never-activated proposal — by domain rule.)
    if rejected_at is not None and activated_at is None:
        return ProposalStatus(
            proposal=proposal,
            status=STATUS_REJECTED,
            activated_at_utc=None,
            rejected_at_utc=rejected_at,
            superseded_by=None,
        )

    if latest_active_event is None:
        return ProposalStatus(
            proposal=proposal,
            status=STATUS_PENDING,
            activated_at_utc=None,
            rejected_at_utc=rejected_at,
            superseded_by=None,
        )

    if latest_active_event.version_id == proposal.version_id:
        return ProposalStatus(
            proposal=proposal,
            status=STATUS_ACTIVE,
            activated_at_utc=activated_at,
            rejected_at_utc=rejected_at,
            superseded_by=None,
        )

    # Latest active event references a different version on the same path.
    if activated_at is not None:
        return ProposalStatus(
            proposal=proposal,
            status=STATUS_SUPERSEDED,
            activated_at_utc=activated_at,
            rejected_at_utc=rejected_at,
            superseded_by=latest_active_event.version_id,
        )

    return ProposalStatus(
        proposal=proposal,
        status=STATUS_PENDING,
        activated_at_utc=None,
        rejected_at_utc=rejected_at,
        superseded_by=None,
    )


# ─── Service ──────────────────────────────────────────────────────────────────


class ApprovalService:
    """Operator-Approval-Operationen auf einem ParameterVersionStore.

    Wraps the store and enforces:
    - explicit non-blank operator id on every state change,
    - non-blank reason on reject (silent rejects break audit posture).

    Optional `snapshot_dir`: nach erfolgreichem ``approve`` / ``rollback``
    wird unter ``<snapshot_dir>/<sanitized_path>.yaml`` ein git-trackbarer
    Snapshot des aktiven parameter_set geschrieben. Default = None (kein
    Snapshot), behaviour-preserving für bestehende Aufrufer.
    """

    def __init__(
        self,
        store: ParameterVersionStore,
        *,
        snapshot_dir: Path | str | None = None,
    ) -> None:
        self._store = store
        self._snapshot_dir: Path | None = (
            Path(snapshot_dir) if snapshot_dir is not None else None
        )

    @property
    def store(self) -> ParameterVersionStore:
        return self._store

    @property
    def snapshot_dir(self) -> Path | None:
        return self._snapshot_dir

    # ----- read ----------------------------------------------------------

    def list_proposals(
        self, parameter_path: str | None = None
    ) -> list[ProposalStatus]:
        """All `version_proposed` records, with computed current status.

        Optionally filter by `parameter_path`.
        """
        records = list(self._store.iter_records())
        proposals = [r for r in records if r.record_type == "version_proposed"]
        if parameter_path is not None:
            proposals = [r for r in proposals if r.parameter_path == parameter_path]
        return [_proposal_status_from_records(records, p) for p in proposals]

    def list_pending(self, parameter_path: str | None = None) -> list[ProposalStatus]:
        """Only proposals whose status is currently 'pending'."""
        return [
            ps for ps in self.list_proposals(parameter_path)
            if ps.status == STATUS_PENDING
        ]

    def get_status(self, version_id: str) -> ProposalStatus | None:
        """Lookup a single proposal by version_id; returns None if not found
        or if version_id doesn't refer to a proposal."""
        records = list(self._store.iter_records())
        for r in records:
            if r.record_type == "version_proposed" and r.version_id == version_id:
                return _proposal_status_from_records(records, r)
        return None

    def latest_active(self, parameter_path: str) -> ParameterChange | None:
        return self._store.latest_active(parameter_path)

    def history(self, parameter_path: str) -> list[ParameterChange]:
        return self._store.history(parameter_path)

    def verify_chain(self) -> tuple[bool, str | None]:
        return self._store.verify_chain()

    # ----- write ---------------------------------------------------------

    def approve(
        self,
        *,
        parameter_path: str,
        version_id: str,
        operator_id: str,
        notes: str | None = None,
    ) -> ParameterChange:
        """Activate a pending proposal.

        Refuses if:
        - operator_id is blank,
        - version_id is unknown for this path,
        - the proposal is currently `rejected` or already `active`,
        - the proposal is `superseded` (operator should rollback instead).
        """
        _require_operator(operator_id)
        status = self.get_status(version_id)
        if status is None or status.proposal.parameter_path != parameter_path:
            raise ValueError(
                f"unknown_proposal:version_id={version_id} path={parameter_path}"
            )
        if status.status == STATUS_REJECTED:
            raise ValueError(
                f"already_rejected:version_id={version_id}"
            )
        if status.status == STATUS_ACTIVE:
            raise ValueError(
                f"already_active:version_id={version_id}"
            )
        if status.status == STATUS_SUPERSEDED:
            raise ValueError(
                f"superseded:version_id={version_id} — use rollback instead"
            )
        record = self._store.activate_version(
            parameter_path=parameter_path,
            version_id=version_id,
            notes=notes,
            created_by=operator_id,
        )
        self._refresh_snapshot(parameter_path, status.proposal, record)
        return record

    def reject(
        self,
        *,
        parameter_path: str,
        version_id: str,
        operator_id: str,
        reason: str,
    ) -> ParameterChange:
        """Mark a pending proposal as rejected.

        Refuses if:
        - operator_id or reason is blank (silent rejects break audit posture),
        - version_id is unknown,
        - the proposal is already active or already rejected.
        """
        _require_operator(operator_id)
        if not reason or not reason.strip():
            raise ValueError("reason_required_for_reject")
        status = self.get_status(version_id)
        if status is None or status.proposal.parameter_path != parameter_path:
            raise ValueError(
                f"unknown_proposal:version_id={version_id} path={parameter_path}"
            )
        if status.status == STATUS_REJECTED:
            raise ValueError(f"already_rejected:version_id={version_id}")
        if status.status == STATUS_ACTIVE:
            raise ValueError(
                f"cannot_reject_active:version_id={version_id} — rollback to a "
                f"different version first"
            )
        return self._store.reject_version(
            parameter_path=parameter_path,
            version_id=version_id,
            reason=reason,
            created_by=operator_id,
        )

    def rollback(
        self,
        *,
        parameter_path: str,
        version_id: str,
        operator_id: str,
        notes: str,
    ) -> ParameterChange:
        """Switch the active version back to a previous proposal.

        Refuses if:
        - operator_id or notes are blank,
        - version_id is unknown,
        - the proposal is currently rejected,
        - the target is already the active version (no-op safety).
        """
        _require_operator(operator_id)
        if not notes or not notes.strip():
            raise ValueError("notes_required_for_rollback")
        status = self.get_status(version_id)
        if status is None or status.proposal.parameter_path != parameter_path:
            raise ValueError(
                f"unknown_proposal:version_id={version_id} path={parameter_path}"
            )
        if status.status == STATUS_REJECTED:
            raise ValueError(
                f"cannot_rollback_to_rejected:version_id={version_id}"
            )
        if status.status == STATUS_ACTIVE:
            raise ValueError(
                f"already_active_no_op:version_id={version_id}"
            )
        record = self._store.rollback_to(
            parameter_path=parameter_path,
            version_id=version_id,
            notes=notes,
            created_by=operator_id,
        )
        self._refresh_snapshot(parameter_path, status.proposal, record)
        return record

    # ----- internals -----------------------------------------------------

    def _refresh_snapshot(
        self,
        parameter_path: str,
        proposal: ParameterChange,
        activation: ParameterChange,
    ) -> None:
        """Write the YAML snapshot if `snapshot_dir` is configured.

        Failures are logged + swallowed: the JSONL audit-chain is the source
        of truth, and the snapshot is derivative. We never break a successful
        approval because of a write failure on the snapshot side.
        """
        if self._snapshot_dir is None:
            return
        try:
            write_snapshot(
                parameter_path=parameter_path,
                parameter_set=dict(proposal.parameter_set),
                version_id=proposal.version_id,
                activated_at_utc=activation.timestamp_utc,
                activated_by=activation.created_by,
                snapshot_dir=self._snapshot_dir,
            )
        except OSError as exc:  # pragma: no cover — IO defence in depth
            import logging

            logging.getLogger(__name__).warning(
                "[approval] snapshot write failed for %s: %s", parameter_path, exc
            )


def _require_operator(operator_id: str) -> None:
    if not operator_id or not operator_id.strip():
        raise ValueError("operator_id_required")


__all__ = [
    "STATUS_ACTIVE",
    "STATUS_PENDING",
    "STATUS_REJECTED",
    "STATUS_SUPERSEDED",
    "ApprovalService",
    "ProposalStatus",
    "ProposalStatusLiteral",
]
