"""Governance gates — Model/Prompt registry + agent permission boundary.

SENTR-owned, fail-closed governance layer. This is **not** a full RBAC/ABAC
system (out of scope while live is disabled and KAI is single-operator /
paper-first). It hardens the few security-critical governance gates that must
hold *before* a model or prompt is allowed to influence a productive decision:

1. :func:`evaluate_model_gate` — a model may only influence productive
   decisions when its registry entry is complete and approved.
2. :func:`evaluate_prompt_gate` — a prompt may only influence productive agent
   decisions when its registry entry is complete, injection-eval'd and approved.
3. :func:`check_agent_capability` / :func:`check_tool_request` — agents may
   analyse, warn, request-cancel and raise risk escalations; they may never
   read live keys, place live orders, self-grant tools, mutate the registry or
   disable audit.
4. :func:`validate_decision_audit` — every productive decision must carry a
   complete registry reference (model_id/version + prompt_id/version +
   approval_status + registry_hash) or the audit event is rejected.

Design invariants (mirrors :mod:`app.release.readiness`):

* **Fail-closed.** Missing evidence == unmet gate == blocked. ``None`` inputs
  never pass.
* **Pure / read-only.** No IO, no secret access, no settings mutation, no
  execution. The gates only inspect the registry entries handed to them — they
  can never themselves read a live key.
* **Machine- and operator-readable.** Every block carries a stable ``code``,
  the offending ``field`` and a human ``message``.
"""

from __future__ import annotations

from app.security.governance.gates import (
    authorize_productive_decision,
    check_agent_capability,
    check_tool_request,
    compute_registry_hash,
    evaluate_model_gate,
    evaluate_prompt_gate,
    validate_decision_audit,
)
from app.security.governance.models import (
    ALLOWED_AGENT_CAPABILITIES,
    FORBIDDEN_AGENT_CAPABILITIES,
    PASSING_INJECTION_STATUSES,
    PRODUCTIVE_MODEL_APPROVALS,
    PRODUCTIVE_PROMPT_APPROVALS,
    AgentCapability,
    DecisionRegistryReference,
    GateResult,
    GovernanceBlocker,
    GovernanceVerdict,
    ModelRegistryEntry,
    PromptRegistryEntry,
)

__all__ = [
    # models
    "AgentCapability",
    "ModelRegistryEntry",
    "PromptRegistryEntry",
    "DecisionRegistryReference",
    "GovernanceBlocker",
    "GateResult",
    "GovernanceVerdict",
    "ALLOWED_AGENT_CAPABILITIES",
    "FORBIDDEN_AGENT_CAPABILITIES",
    "PRODUCTIVE_MODEL_APPROVALS",
    "PRODUCTIVE_PROMPT_APPROVALS",
    "PASSING_INJECTION_STATUSES",
    # gates
    "evaluate_model_gate",
    "evaluate_prompt_gate",
    "check_agent_capability",
    "check_tool_request",
    "validate_decision_audit",
    "compute_registry_hash",
    "authorize_productive_decision",
]
