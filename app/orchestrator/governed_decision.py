"""Governed decision append — Issue #165 productive wiring.

Wires the SENTR governance gates (PR #164) into the decision-journal append
path. A *governed* decision must pass model + prompt + tool authorization and
carry a complete :class:`DecisionRegistryReference` before any journal record is
written. Fail-closed: an unauthorized or incompletely-referenced decision
produces **no** journal record.

This is an additive path. The existing
:func:`app.orchestrator.decision_journal.append_decision_jsonl` is unchanged
(legacy/back-compat); callers opt in to governance via
:func:`authorize_and_append_decision`. The registry reference is persisted in the
governance audit sidecar (keyed by ``decision_id``) rather than mutating the
frozen canonical decision-record schema.

Invariants (Issue #165): no ``entry_mode`` change; agents get no registry
mutation right (the registry writers live in
:mod:`app.security.governance.registry_store` and are operator/CLI-only); the
gates are pure/read-only.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from app.orchestrator.decision_journal import DecisionInstance, append_decision_jsonl
from app.security.governance.gates import (
    authorize_productive_decision,
    validate_decision_audit,
)
from app.security.governance.models import (
    DecisionRegistryReference,
    GovernanceVerdict,
    ModelRegistryEntry,
    PromptRegistryEntry,
)
from app.security.governance.registry_store import (
    DECISION_GOVERNANCE_AUDIT_PATH,
    append_decision_governance_audit,
    load_model_registry,
    load_prompt_registry,
    lookup_model,
    lookup_prompt,
)

logger = logging.getLogger(__name__)


class GovernanceRejectedError(Exception):
    """Raised when a decision fails governance authorization — fail-closed, so no
    journal record is written. Carries the verdict for the caller/audit."""

    def __init__(self, verdict: GovernanceVerdict, *, audit_blocker_codes: list[str]) -> None:
        self.verdict = verdict
        self.audit_blocker_codes = audit_blocker_codes
        codes = sorted({*[b.code for b in verdict.blockers], *audit_blocker_codes})
        super().__init__(f"decision governance rejected: {', '.join(codes) or 'unauthorized'}")


def authorize_and_append_decision(
    decision: DecisionInstance,
    journal_path: Path | str,
    *,
    model_entry: ModelRegistryEntry | None,
    prompt_entry: PromptRegistryEntry | None,
    requested_tools: tuple[str, ...] = (),
    governance_audit_path: Path | str = DECISION_GOVERNANCE_AUDIT_PATH,
    timestamp_utc: str | None = None,
) -> GovernanceVerdict:
    """Authorize a decision and append it ONLY when governance passes.

    Steps (all fail-closed):
      1. ``authorize_productive_decision`` (model + prompt + tool gates).
      2. ``validate_decision_audit`` over the produced registry reference.
      3. On pass → append the journal record, then persist the registry
         reference to the governance audit sidecar (keyed by ``decision_id``).
      4. On fail → write a refusal audit record (authorized=False, NO journal
         record) and raise :class:`GovernanceRejectedError`.

    Returns the :class:`GovernanceVerdict` on success.
    """
    ts = timestamp_utc or datetime.now(UTC).isoformat()
    verdict = authorize_productive_decision(
        model_entry, prompt_entry, requested_tools=requested_tools
    )
    reference = verdict.registry_reference
    audit_gate = validate_decision_audit(reference)

    if not verdict.authorized or not audit_gate.allowed:
        # Fail-closed: record the refusal for auditability, write NO journal row.
        refusal_ref = reference or DecisionRegistryReference()
        audit_blockers = [b.code for b in audit_gate.blockers]
        append_decision_governance_audit(
            decision_id=str(decision.decision_id),
            reference=refusal_ref,
            authorized=False,
            blocker_codes=[b.code for b in verdict.blockers] + audit_blockers,
            timestamp_utc=ts,
            path=governance_audit_path,
        )
        logger.warning(
            "decision_governance_rejected: decision_id=%s blockers=%s",
            decision.decision_id,
            sorted({*[b.code for b in verdict.blockers], *audit_blockers}),
        )
        raise GovernanceRejectedError(verdict, audit_blocker_codes=audit_blockers)

    # Authorized: the reference is guaranteed complete here (audit_gate passed).
    assert reference is not None  # noqa: S101 — invariant guarded by audit_gate
    append_decision_jsonl(decision, journal_path)
    append_decision_governance_audit(
        decision_id=str(decision.decision_id),
        reference=reference,
        authorized=True,
        blocker_codes=[],
        timestamp_utc=ts,
        path=governance_audit_path,
    )
    return verdict


def resolve_and_append_decision(
    decision: DecisionInstance,
    journal_path: Path | str,
    *,
    model_id: str,
    model_version: str,
    prompt_id: str,
    prompt_version: str,
    requested_tools: tuple[str, ...] = (),
    governance_audit_path: Path | str = DECISION_GOVERNANCE_AUDIT_PATH,
    timestamp_utc: str | None = None,
) -> GovernanceVerdict:
    """Resolve model/prompt entries from the persisted registries, then govern.

    A model/prompt identity that is not in the registry resolves to ``None`` →
    the gate refuses (fail-closed: an unknown model can never run a productive
    decision)."""
    model_entry = lookup_model(load_model_registry(), model_id, model_version)
    prompt_entry = lookup_prompt(load_prompt_registry(), prompt_id, prompt_version)
    return authorize_and_append_decision(
        decision,
        journal_path,
        model_entry=model_entry,
        prompt_entry=prompt_entry,
        requested_tools=requested_tools,
        governance_audit_path=governance_audit_path,
        timestamp_utc=timestamp_utc,
    )


__all__ = [
    "GovernanceRejectedError",
    "authorize_and_append_decision",
    "resolve_and_append_decision",
]
