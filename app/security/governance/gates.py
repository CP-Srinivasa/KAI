"""Governance gate logic — pure, fail-closed.

No IO, no secret access, no settings mutation. Each gate inspects only the
registry entry / capability handed to it and returns a machine- and operator-
readable :class:`GateResult`. The combined :func:`authorize_productive_decision`
produces the audit registry reference *only* when every gate passes.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence

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

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _present(value: str | None) -> bool:
    """A required string field counts as present only when non-blank."""
    return isinstance(value, str) and bool(value.strip())


def _normalize(value: str | None) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


# ---------------------------------------------------------------------------
# 1. Model Registry Gate
# ---------------------------------------------------------------------------

# Required non-blank string fields for a model registry entry.
_MODEL_REQUIRED_FIELDS: tuple[str, ...] = (
    "model_id",
    "version",
    "eval_suite_id",
    "risk_rating",
    "owner",
    "last_validation_at",
)


def evaluate_model_gate(entry: ModelRegistryEntry | None) -> GateResult:
    """A model may influence productive decisions only when its registry entry
    is complete and ``approval_status`` is in
    :data:`PRODUCTIVE_MODEL_APPROVALS`. Fail-closed: ``None`` is blocked."""
    gate = "model_registry"
    if entry is None:
        return GateResult(
            allowed=False,
            gate=gate,
            blockers=(
                GovernanceBlocker(
                    code="MODEL_ENTRY_MISSING",
                    field="entry",
                    message="no model registry entry on record — fail-closed",
                ),
            ),
        )

    blockers: list[GovernanceBlocker] = []
    for fname in _MODEL_REQUIRED_FIELDS:
        if not _present(getattr(entry, fname)):
            blockers.append(
                GovernanceBlocker(
                    code=f"MODEL_{fname.upper()}_MISSING",
                    field=fname,
                    message=f"model registry field '{fname}' is missing or blank",
                )
            )

    # approval_status: present AND productive.
    if not _present(entry.approval_status):
        blockers.append(
            GovernanceBlocker(
                code="MODEL_APPROVAL_STATUS_MISSING",
                field="approval_status",
                message="model approval_status is missing or blank",
            )
        )
    elif _normalize(entry.approval_status) not in PRODUCTIVE_MODEL_APPROVALS:
        blockers.append(
            GovernanceBlocker(
                code="MODEL_APPROVAL_STATUS_NOT_PRODUCTIVE",
                field="approval_status",
                message=(
                    f"model approval_status='{entry.approval_status}' is not in "
                    f"{sorted(PRODUCTIVE_MODEL_APPROVALS)}"
                ),
            )
        )

    return GateResult(allowed=not blockers, gate=gate, blockers=tuple(blockers))


# ---------------------------------------------------------------------------
# 2. Prompt Registry Gate
# ---------------------------------------------------------------------------

_PROMPT_REQUIRED_STR_FIELDS: tuple[str, ...] = (
    "prompt_id",
    "prompt_version",
    "owner_agent",
    "output_contract",
)


def evaluate_prompt_gate(entry: PromptRegistryEntry | None) -> GateResult:
    """A prompt may influence productive agent decisions only when its registry
    entry is complete, the tool allow/forbid lists are *defined* and disjoint,
    the prompt-injection eval passed, and ``approval_status`` is freigegeben.
    Fail-closed: ``None`` is blocked."""
    gate = "prompt_registry"
    if entry is None:
        return GateResult(
            allowed=False,
            gate=gate,
            blockers=(
                GovernanceBlocker(
                    code="PROMPT_ENTRY_MISSING",
                    field="entry",
                    message="no prompt registry entry on record — fail-closed",
                ),
            ),
        )

    blockers: list[GovernanceBlocker] = []
    for fname in _PROMPT_REQUIRED_STR_FIELDS:
        if not _present(getattr(entry, fname)):
            blockers.append(
                GovernanceBlocker(
                    code=f"PROMPT_{fname.upper()}_MISSING",
                    field=fname,
                    message=f"prompt registry field '{fname}' is missing or blank",
                )
            )

    # Tool lists must be *defined* (None == undefined). An empty tuple is a
    # valid definition (prompt may use no tools).
    if entry.allowed_tools is None:
        blockers.append(
            GovernanceBlocker(
                code="PROMPT_ALLOWED_TOOLS_UNDEFINED",
                field="allowed_tools",
                message="prompt allowed_tools is undefined (None)",
            )
        )
    if entry.forbidden_tools is None:
        blockers.append(
            GovernanceBlocker(
                code="PROMPT_FORBIDDEN_TOOLS_UNDEFINED",
                field="forbidden_tools",
                message="prompt forbidden_tools is undefined (None)",
            )
        )
    # Consistency: a tool cannot be both allowed and forbidden.
    if entry.allowed_tools is not None and entry.forbidden_tools is not None:
        overlap = sorted(set(entry.allowed_tools) & set(entry.forbidden_tools))
        if overlap:
            blockers.append(
                GovernanceBlocker(
                    code="PROMPT_TOOL_LIST_OVERLAP",
                    field="allowed_tools",
                    message=f"tools appear in both allowed and forbidden: {overlap}",
                )
            )

    # Prompt-injection eval must have explicitly passed.
    if not _present(entry.prompt_injection_eval_status):
        blockers.append(
            GovernanceBlocker(
                code="PROMPT_INJECTION_EVAL_MISSING",
                field="prompt_injection_eval_status",
                message="prompt_injection_eval_status is missing or blank",
            )
        )
    elif _normalize(entry.prompt_injection_eval_status) not in PASSING_INJECTION_STATUSES:
        blockers.append(
            GovernanceBlocker(
                code="PROMPT_INJECTION_EVAL_NOT_PASSED",
                field="prompt_injection_eval_status",
                message=(
                    f"prompt_injection_eval_status='{entry.prompt_injection_eval_status}' "
                    f"is not in {sorted(PASSING_INJECTION_STATUSES)}"
                ),
            )
        )

    # approval_status: present AND freigegeben.
    if not _present(entry.approval_status):
        blockers.append(
            GovernanceBlocker(
                code="PROMPT_APPROVAL_STATUS_MISSING",
                field="approval_status",
                message="prompt approval_status is missing or blank",
            )
        )
    elif _normalize(entry.approval_status) not in PRODUCTIVE_PROMPT_APPROVALS:
        blockers.append(
            GovernanceBlocker(
                code="PROMPT_APPROVAL_STATUS_NOT_RELEASED",
                field="approval_status",
                message=(
                    f"prompt approval_status='{entry.approval_status}' is not freigegeben "
                    f"(not in {sorted(PRODUCTIVE_PROMPT_APPROVALS)})"
                ),
            )
        )

    return GateResult(allowed=not blockers, gate=gate, blockers=tuple(blockers))


# ---------------------------------------------------------------------------
# 3. Agent Permission Boundary
# ---------------------------------------------------------------------------


def check_agent_capability(capability: str | AgentCapability) -> GateResult:
    """Allow only the four permitted agent capabilities (analyse / warn /
    request-cancel / raise-risk-escalation). Everything else — the named
    forbidden capabilities *and* any unknown capability — is denied. Fail-closed.

    This is the boundary that blocks an agent attempting to read live keys,
    place live orders, self-grant tools, mutate the registry or disable audit.
    """
    gate = "agent_capability"
    raw = capability.value if isinstance(capability, AgentCapability) else str(capability)
    norm = _normalize(raw)

    if norm in ALLOWED_AGENT_CAPABILITIES:
        return GateResult(allowed=True, gate=gate, blockers=())

    if norm in FORBIDDEN_AGENT_CAPABILITIES:
        return GateResult(
            allowed=False,
            gate=gate,
            blockers=(
                GovernanceBlocker(
                    code="AGENT_CAPABILITY_FORBIDDEN",
                    field="capability",
                    message=(
                        f"capability '{raw}' is on the forbidden boundary "
                        f"(agents may only: {sorted(ALLOWED_AGENT_CAPABILITIES)})"
                    ),
                ),
            ),
        )

    # Unknown capability — fail-closed deny.
    return GateResult(
        allowed=False,
        gate=gate,
        blockers=(
            GovernanceBlocker(
                code="AGENT_CAPABILITY_UNKNOWN",
                field="capability",
                message=(
                    f"capability '{raw}' is not an allowed capability "
                    f"(allowed: {sorted(ALLOWED_AGENT_CAPABILITIES)}) — fail-closed"
                ),
            ),
        ),
    )


def check_tool_request(
    prompt_entry: PromptRegistryEntry | None,
    requested_tool: str,
) -> GateResult:
    """A prompt may only use a tool that is on its allowlist and not on its
    forbidden list. This is the gate that catches a prompt-injection attempt to
    escalate into a tool the prompt was never granted. Fail-closed: an undefined
    allowlist denies everything.
    """
    gate = "tool_request"
    tool = (requested_tool or "").strip()
    if not tool:
        return GateResult(
            allowed=False,
            gate=gate,
            blockers=(
                GovernanceBlocker(
                    code="TOOL_REQUEST_BLANK",
                    field="requested_tool",
                    message="requested tool is blank",
                ),
            ),
        )

    if prompt_entry is None or prompt_entry.allowed_tools is None:
        return GateResult(
            allowed=False,
            gate=gate,
            blockers=(
                GovernanceBlocker(
                    code="TOOL_ALLOWLIST_UNDEFINED",
                    field="allowed_tools",
                    message=(
                        f"tool '{tool}' requested but the prompt has no defined "
                        "allowlist — fail-closed"
                    ),
                ),
            ),
        )

    forbidden = set(prompt_entry.forbidden_tools or ())
    if tool in forbidden:
        return GateResult(
            allowed=False,
            gate=gate,
            blockers=(
                GovernanceBlocker(
                    code="TOOL_FORBIDDEN",
                    field="forbidden_tools",
                    message=f"tool '{tool}' is on the prompt's forbidden list — escalation denied",
                ),
            ),
        )

    if tool not in set(prompt_entry.allowed_tools):
        return GateResult(
            allowed=False,
            gate=gate,
            blockers=(
                GovernanceBlocker(
                    code="TOOL_NOT_ALLOWLISTED",
                    field="allowed_tools",
                    message=(
                        f"tool '{tool}' is not on the prompt's allowlist "
                        f"{sorted(prompt_entry.allowed_tools)} — escalation denied"
                    ),
                ),
            ),
        )

    return GateResult(allowed=True, gate=gate, blockers=())


# ---------------------------------------------------------------------------
# Registry hash + 4. Audit reference validation
# ---------------------------------------------------------------------------


def compute_registry_hash(
    model_entry: ModelRegistryEntry,
    prompt_entry: PromptRegistryEntry,
) -> str:
    """Deterministic SHA-256 over the canonical JSON of the (model, prompt)
    registry pair. Tamper-evident: any registry field change changes the hash,
    so an audit event's ``registry_hash`` pins the exact governance posture the
    decision ran under. Algorithm matches :mod:`app.audit.decision_chain`."""
    payload = {
        "model": {
            "model_id": model_entry.model_id,
            "version": model_entry.version,
            "eval_suite_id": model_entry.eval_suite_id,
            "approval_status": model_entry.approval_status,
            "risk_rating": model_entry.risk_rating,
            "owner": model_entry.owner,
            "last_validation_at": model_entry.last_validation_at,
        },
        "prompt": {
            "prompt_id": prompt_entry.prompt_id,
            "prompt_version": prompt_entry.prompt_version,
            "owner_agent": prompt_entry.owner_agent,
            "allowed_tools": (
                list(prompt_entry.allowed_tools) if prompt_entry.allowed_tools is not None else None
            ),
            "forbidden_tools": (
                list(prompt_entry.forbidden_tools)
                if prompt_entry.forbidden_tools is not None
                else None
            ),
            "output_contract": prompt_entry.output_contract,
            "prompt_injection_eval_status": prompt_entry.prompt_injection_eval_status,
            "approval_status": prompt_entry.approval_status,
        },
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# Required fields every productive decision's audit registry reference must carry.
_AUDIT_REQUIRED_FIELDS: tuple[str, ...] = (
    "model_id",
    "model_version",
    "prompt_id",
    "prompt_version",
    "approval_status",
    "registry_hash",
)


def validate_decision_audit(reference: DecisionRegistryReference | None) -> GateResult:
    """Every productive decision's audit event must carry a complete registry
    reference. A missing or incomplete reference is rejected. Fail-closed."""
    gate = "decision_audit"
    if reference is None:
        return GateResult(
            allowed=False,
            gate=gate,
            blockers=(
                GovernanceBlocker(
                    code="AUDIT_REGISTRY_REF_MISSING",
                    field="reference",
                    message="audit event has no registry reference — rejected (fail-closed)",
                ),
            ),
        )

    blockers: list[GovernanceBlocker] = []
    for fname in _AUDIT_REQUIRED_FIELDS:
        if not _present(getattr(reference, fname)):
            blockers.append(
                GovernanceBlocker(
                    code=f"AUDIT_{fname.upper()}_MISSING",
                    field=fname,
                    message=f"audit registry reference field '{fname}' is missing or blank",
                )
            )

    # registry_hash must look like a SHA-256 hex digest when present.
    if _present(reference.registry_hash) and not _SHA256_HEX_RE.match(
        reference.registry_hash or ""
    ):
        blockers.append(
            GovernanceBlocker(
                code="AUDIT_REGISTRY_HASH_MALFORMED",
                field="registry_hash",
                message="registry_hash is not a 64-char lowercase SHA-256 hex digest",
            )
        )

    return GateResult(allowed=not blockers, gate=gate, blockers=tuple(blockers))


# ---------------------------------------------------------------------------
# Combined authorization (model + prompt + tools -> audit reference)
# ---------------------------------------------------------------------------


def authorize_productive_decision(
    model_entry: ModelRegistryEntry | None,
    prompt_entry: PromptRegistryEntry | None,
    *,
    requested_tools: Sequence[str] = (),
) -> GovernanceVerdict:
    """Run the model gate + prompt gate + per-tool checks and, only when *all*
    pass, produce a complete :class:`DecisionRegistryReference` (with computed
    ``registry_hash``) for the decision's audit event.

    Fail-closed: any failing gate yields ``authorized=False`` and
    ``registry_reference=None`` with the aggregated blockers.
    """
    model_gate = evaluate_model_gate(model_entry)
    prompt_gate = evaluate_prompt_gate(prompt_entry)

    tool_gates: list[GateResult] = []
    for tool in requested_tools:
        tool_gates.append(check_tool_request(prompt_entry, tool))

    blockers: list[GovernanceBlocker] = []
    blockers.extend(model_gate.blockers)
    blockers.extend(prompt_gate.blockers)
    for tg in tool_gates:
        blockers.extend(tg.blockers)

    authorized = model_gate.allowed and prompt_gate.allowed and all(tg.allowed for tg in tool_gates)

    reference: DecisionRegistryReference | None = None
    if authorized and model_entry is not None and prompt_entry is not None:
        reference = DecisionRegistryReference(
            model_id=model_entry.model_id,
            model_version=model_entry.version,
            prompt_id=prompt_entry.prompt_id,
            prompt_version=prompt_entry.prompt_version,
            approval_status=model_entry.approval_status,
            registry_hash=compute_registry_hash(model_entry, prompt_entry),
        )

    return GovernanceVerdict(
        authorized=authorized,
        blockers=tuple(blockers),
        model_gate=model_gate,
        prompt_gate=prompt_gate,
        tool_gates=tuple(tool_gates),
        registry_reference=reference,
    )
