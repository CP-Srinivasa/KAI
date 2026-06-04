"""Governance data models — registry entries, capabilities, gate results.

All entry models use ``frozen=True`` dataclasses with every field defaulting to
the fail-closed value (``None``). An empty instance therefore yields a *blocked*
posture — exactly mirroring :class:`app.release.readiness.LiveReadinessEvidence`.

The distinction between ``None`` and an empty collection is deliberate and load-
bearing for the prompt gate: ``allowed_tools=None`` means "tool allowlist not
defined" (a blocker), while ``allowed_tools=()`` means "explicitly defined as
empty" (the prompt may use *no* tools — defined, so not a blocker).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# ---------------------------------------------------------------------------
# Approval / eval status vocabularies
# ---------------------------------------------------------------------------


class ModelApprovalStatus(StrEnum):
    """Lifecycle of a model's approval. Only the productive set may influence
    productive decisions; everything else fails the model gate."""

    DRAFT = "draft"
    SHADOW_APPROVED = "shadow_approved"
    PRODUCTION_APPROVED = "production_approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


# Per the goal spec: a model may influence productive decisions only when
# approval_status in {shadow_approved, production_approved}.
PRODUCTIVE_MODEL_APPROVALS: frozenset[str] = frozenset(
    {
        ModelApprovalStatus.SHADOW_APPROVED.value,
        ModelApprovalStatus.PRODUCTION_APPROVED.value,
    }
)


class PromptApprovalStatus(StrEnum):
    """Lifecycle of a prompt's approval ("freigegeben" == approved)."""

    DRAFT = "draft"
    SHADOW_APPROVED = "shadow_approved"
    APPROVED = "approved"
    PRODUCTION_APPROVED = "production_approved"
    REJECTED = "rejected"


# The goal spec for prompts says only "approval_status freigegeben". We read
# that literally and strictly: a shadow-approved prompt is NOT yet freigegeben
# for productive agent decisions. Keep this stricter than the model set on
# purpose — a prompt drives *tool-bearing* agent behaviour, so the bar is higher.
PRODUCTIVE_PROMPT_APPROVALS: frozenset[str] = frozenset(
    {
        PromptApprovalStatus.APPROVED.value,
        PromptApprovalStatus.PRODUCTION_APPROVED.value,
    }
)


class PromptInjectionEvalStatus(StrEnum):
    """Result of the prompt-injection evaluation suite for a prompt."""

    NOT_RUN = "not_run"
    PENDING = "pending"
    FAILED = "failed"
    PASSED = "passed"


# Only an explicitly passed injection eval clears the gate. Missing / pending /
# failed all fail-closed.
PASSING_INJECTION_STATUSES: frozenset[str] = frozenset({PromptInjectionEvalStatus.PASSED.value})


# ---------------------------------------------------------------------------
# Agent permission boundary
# ---------------------------------------------------------------------------


class AgentCapability(StrEnum):
    """Capabilities an agent may *attempt*. The allowed set is the only thing
    that passes :func:`app.security.governance.gates.check_agent_capability`;
    the forbidden set is enumerated so denied attempts are auditable by name and
    an unknown capability fails-closed (denied)."""

    # --- allowed ---------------------------------------------------------
    ANALYZE = "analyze"
    WARN = "warn"
    REQUEST_CANCEL = "request_cancel"
    RAISE_RISK_ESCALATION = "raise_risk_escalation"

    # --- forbidden (named for auditability of denied attempts) -----------
    READ_LIVE_KEYS = "read_live_keys"
    PLACE_LIVE_ORDER = "place_live_order"
    GRANT_OWN_TOOLS = "grant_own_tools"
    MUTATE_REGISTRY = "mutate_registry"
    DISABLE_AUDIT = "disable_audit"


ALLOWED_AGENT_CAPABILITIES: frozenset[str] = frozenset(
    {
        AgentCapability.ANALYZE.value,
        AgentCapability.WARN.value,
        AgentCapability.REQUEST_CANCEL.value,
        AgentCapability.RAISE_RISK_ESCALATION.value,
    }
)

FORBIDDEN_AGENT_CAPABILITIES: frozenset[str] = frozenset(
    {
        AgentCapability.READ_LIVE_KEYS.value,
        AgentCapability.PLACE_LIVE_ORDER.value,
        AgentCapability.GRANT_OWN_TOOLS.value,
        AgentCapability.MUTATE_REGISTRY.value,
        AgentCapability.DISABLE_AUDIT.value,
    }
)


# ---------------------------------------------------------------------------
# Registry entries (fail-closed: every field defaults to None)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelRegistryEntry:
    """Registry record for a model. A model may influence productive decisions
    only when *every* field below is present and ``approval_status`` is in
    :data:`PRODUCTIVE_MODEL_APPROVALS`."""

    model_id: str | None = None
    version: str | None = None
    eval_suite_id: str | None = None
    approval_status: str | None = None
    risk_rating: str | None = None
    owner: str | None = None
    last_validation_at: str | None = None


@dataclass(frozen=True)
class PromptRegistryEntry:
    """Registry record for a prompt. ``allowed_tools`` / ``forbidden_tools`` are
    ``None`` when undefined (a blocker) vs an empty tuple when defined-as-empty
    (allowed)."""

    prompt_id: str | None = None
    prompt_version: str | None = None
    owner_agent: str | None = None
    allowed_tools: tuple[str, ...] | None = None
    forbidden_tools: tuple[str, ...] | None = None
    output_contract: str | None = None
    prompt_injection_eval_status: str | None = None
    approval_status: str | None = None


@dataclass(frozen=True)
class DecisionRegistryReference:
    """The registry reference that every productive decision's audit event must
    carry. :func:`validate_decision_audit` rejects an event whose reference is
    missing or incomplete."""

    model_id: str | None = None
    model_version: str | None = None
    prompt_id: str | None = None
    prompt_version: str | None = None
    approval_status: str | None = None
    registry_hash: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "model_id": self.model_id,
            "model_version": self.model_version,
            "prompt_id": self.prompt_id,
            "prompt_version": self.prompt_version,
            "approval_status": self.approval_status,
            "registry_hash": self.registry_hash,
        }


# ---------------------------------------------------------------------------
# Gate results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GovernanceBlocker:
    """A single unmet governance condition. ``code`` is machine-readable,
    ``field`` names the offending input, ``message`` is human."""

    code: str
    field: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "field": self.field, "message": self.message}


@dataclass(frozen=True)
class GateResult:
    """Result of a single gate. ``allowed`` is only ``True`` when ``blockers``
    is empty."""

    allowed: bool
    gate: str
    blockers: tuple[GovernanceBlocker, ...] = ()

    @property
    def blocker_codes(self) -> tuple[str, ...]:
        return tuple(b.code for b in self.blockers)

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "gate": self.gate,
            "blockers": [b.to_dict() for b in self.blockers],
            "blocker_codes": sorted(self.blocker_codes),
        }


@dataclass(frozen=True)
class GovernanceVerdict:
    """Combined verdict over model gate + prompt gate + requested tools.

    When ``authorized`` is ``True`` a complete :class:`DecisionRegistryReference`
    is produced (with a computed ``registry_hash``) for the decision's audit
    event. When ``authorized`` is ``False`` the reference is ``None`` and the
    aggregated ``blockers`` explain why — fail-closed."""

    authorized: bool
    blockers: tuple[GovernanceBlocker, ...]
    model_gate: GateResult
    prompt_gate: GateResult
    tool_gates: tuple[GateResult, ...] = ()
    registry_reference: DecisionRegistryReference | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "authorized": self.authorized,
            "blockers": [b.to_dict() for b in self.blockers],
            "blocker_codes": sorted(b.code for b in self.blockers),
            "model_gate": self.model_gate.to_dict(),
            "prompt_gate": self.prompt_gate.to_dict(),
            "tool_gates": [g.to_dict() for g in self.tool_gates],
            "registry_reference": (
                self.registry_reference.to_dict() if self.registry_reference else None
            ),
        }
