"""Tests for SENTR governance gates (model/prompt registry + agent boundary).

Covers the five required adversarial scenarios:

1. Agent nutzt einen nicht freigegebenen Prompt           -> prompt gate blocks
2. Modell ohne approval_status wird blockiert             -> model gate blocks
3. Prompt-Injection fordert Tool-Eskalation               -> tool gate denies
4. Agent versucht Live-Key-Zugriff                        -> capability boundary denies
5. Audit-Event ohne Registry-Reference wird abgelehnt     -> audit gate rejects

plus the happy paths and fail-closed edge cases that prove the gates are not
merely structural.
"""

from __future__ import annotations

import pytest

from app.security.governance import (
    ALLOWED_AGENT_CAPABILITIES,
    FORBIDDEN_AGENT_CAPABILITIES,
    AgentCapability,
    DecisionRegistryReference,
    ModelRegistryEntry,
    PromptRegistryEntry,
    authorize_productive_decision,
    check_agent_capability,
    check_tool_request,
    compute_registry_hash,
    evaluate_model_gate,
    evaluate_prompt_gate,
    validate_decision_audit,
)

# ---------------------------------------------------------------------------
# Fixtures — fully valid, productive entries (the gates must pass these).
# ---------------------------------------------------------------------------


def _valid_model() -> ModelRegistryEntry:
    return ModelRegistryEntry(
        model_id="internal-rule-heuristic",
        version="v1",
        eval_suite_id="eval-2026-06",
        approval_status="production_approved",
        risk_rating="low",
        owner="sentr",
        last_validation_at="2026-06-05T00:00:00Z",
    )


def _valid_prompt() -> PromptRegistryEntry:
    return PromptRegistryEntry(
        prompt_id="signal-analyst",
        prompt_version="v3",
        owner_agent="neo",
        allowed_tools=("canonical_read", "append_decision_instance"),
        forbidden_tools=("guarded_write", "place_live_order"),
        output_contract="LLMAnalysisOutput",
        prompt_injection_eval_status="passed",
        approval_status="approved",
    )


# ---------------------------------------------------------------------------
# 1. Model Registry Gate
# ---------------------------------------------------------------------------


def test_valid_model_passes_gate() -> None:
    result = evaluate_model_gate(_valid_model())
    assert result.allowed is True
    assert result.blockers == ()


def test_model_without_approval_status_is_blocked() -> None:
    """Required scenario 2: a model missing approval_status must be blocked."""
    entry = ModelRegistryEntry(
        model_id="m1",
        version="v1",
        eval_suite_id="e1",
        approval_status=None,  # <- missing
        risk_rating="low",
        owner="sentr",
        last_validation_at="2026-06-05T00:00:00Z",
    )
    result = evaluate_model_gate(entry)
    assert result.allowed is False
    assert "MODEL_APPROVAL_STATUS_MISSING" in result.blocker_codes


def test_model_with_non_productive_approval_is_blocked() -> None:
    entry = ModelRegistryEntry(
        model_id="m1",
        version="v1",
        eval_suite_id="e1",
        approval_status="draft",  # present but not productive
        risk_rating="low",
        owner="sentr",
        last_validation_at="2026-06-05T00:00:00Z",
    )
    result = evaluate_model_gate(entry)
    assert result.allowed is False
    assert "MODEL_APPROVAL_STATUS_NOT_PRODUCTIVE" in result.blocker_codes


def test_model_none_is_fail_closed() -> None:
    result = evaluate_model_gate(None)
    assert result.allowed is False
    assert "MODEL_ENTRY_MISSING" in result.blocker_codes


def test_model_blank_field_is_blocked() -> None:
    entry = ModelRegistryEntry(
        model_id="   ",  # blank counts as missing
        version="v1",
        eval_suite_id="e1",
        approval_status="shadow_approved",
        risk_rating="low",
        owner="sentr",
        last_validation_at="2026-06-05T00:00:00Z",
    )
    result = evaluate_model_gate(entry)
    assert result.allowed is False
    assert "MODEL_MODEL_ID_MISSING" in result.blocker_codes


def test_shadow_approved_model_is_productive() -> None:
    entry = _valid_model()
    entry = ModelRegistryEntry(
        model_id=entry.model_id,
        version=entry.version,
        eval_suite_id=entry.eval_suite_id,
        approval_status="shadow_approved",
        risk_rating=entry.risk_rating,
        owner=entry.owner,
        last_validation_at=entry.last_validation_at,
    )
    assert evaluate_model_gate(entry).allowed is True


# ---------------------------------------------------------------------------
# 2. Prompt Registry Gate
# ---------------------------------------------------------------------------


def test_valid_prompt_passes_gate() -> None:
    result = evaluate_prompt_gate(_valid_prompt())
    assert result.allowed is True
    assert result.blockers == ()


def test_agent_uses_unapproved_prompt_is_blocked() -> None:
    """Required scenario 1: an agent using a not-freigegeben prompt is blocked."""
    prompt = _valid_prompt()
    unapproved = PromptRegistryEntry(
        prompt_id=prompt.prompt_id,
        prompt_version=prompt.prompt_version,
        owner_agent=prompt.owner_agent,
        allowed_tools=prompt.allowed_tools,
        forbidden_tools=prompt.forbidden_tools,
        output_contract=prompt.output_contract,
        prompt_injection_eval_status=prompt.prompt_injection_eval_status,
        approval_status="draft",  # <- not freigegeben
    )
    result = evaluate_prompt_gate(unapproved)
    assert result.allowed is False
    assert "PROMPT_APPROVAL_STATUS_NOT_RELEASED" in result.blocker_codes


def test_prompt_without_injection_eval_is_blocked() -> None:
    prompt = _valid_prompt()
    not_evald = PromptRegistryEntry(
        prompt_id=prompt.prompt_id,
        prompt_version=prompt.prompt_version,
        owner_agent=prompt.owner_agent,
        allowed_tools=prompt.allowed_tools,
        forbidden_tools=prompt.forbidden_tools,
        output_contract=prompt.output_contract,
        prompt_injection_eval_status="pending",  # not passed
        approval_status=prompt.approval_status,
    )
    result = evaluate_prompt_gate(not_evald)
    assert result.allowed is False
    assert "PROMPT_INJECTION_EVAL_NOT_PASSED" in result.blocker_codes


def test_prompt_undefined_tool_lists_are_blocked() -> None:
    prompt = PromptRegistryEntry(
        prompt_id="p1",
        prompt_version="v1",
        owner_agent="neo",
        allowed_tools=None,  # undefined
        forbidden_tools=None,  # undefined
        output_contract="X",
        prompt_injection_eval_status="passed",
        approval_status="approved",
    )
    result = evaluate_prompt_gate(prompt)
    assert result.allowed is False
    assert "PROMPT_ALLOWED_TOOLS_UNDEFINED" in result.blocker_codes
    assert "PROMPT_FORBIDDEN_TOOLS_UNDEFINED" in result.blocker_codes


def test_prompt_empty_tool_lists_are_defined_not_blocked() -> None:
    """Empty tuple == defined-as-empty (no tools), which is allowed; only None
    (undefined) is a blocker."""
    prompt = PromptRegistryEntry(
        prompt_id="p1",
        prompt_version="v1",
        owner_agent="neo",
        allowed_tools=(),
        forbidden_tools=(),
        output_contract="X",
        prompt_injection_eval_status="passed",
        approval_status="approved",
    )
    result = evaluate_prompt_gate(prompt)
    assert result.allowed is True


def test_prompt_tool_list_overlap_is_blocked() -> None:
    prompt = PromptRegistryEntry(
        prompt_id="p1",
        prompt_version="v1",
        owner_agent="neo",
        allowed_tools=("canonical_read", "guarded_write"),
        forbidden_tools=("guarded_write",),  # same tool both lists
        output_contract="X",
        prompt_injection_eval_status="passed",
        approval_status="approved",
    )
    result = evaluate_prompt_gate(prompt)
    assert result.allowed is False
    assert "PROMPT_TOOL_LIST_OVERLAP" in result.blocker_codes


def test_prompt_none_is_fail_closed() -> None:
    result = evaluate_prompt_gate(None)
    assert result.allowed is False
    assert "PROMPT_ENTRY_MISSING" in result.blocker_codes


# ---------------------------------------------------------------------------
# 3. Agent Permission Boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "capability",
    [
        AgentCapability.ANALYZE,
        AgentCapability.WARN,
        AgentCapability.REQUEST_CANCEL,
        AgentCapability.RAISE_RISK_ESCALATION,
    ],
)
def test_allowed_capabilities_pass(capability: AgentCapability) -> None:
    assert check_agent_capability(capability).allowed is True


def test_agent_live_key_access_is_denied() -> None:
    """Required scenario 4: an agent attempting live-key access is denied."""
    result = check_agent_capability(AgentCapability.READ_LIVE_KEYS)
    assert result.allowed is False
    assert "AGENT_CAPABILITY_FORBIDDEN" in result.blocker_codes


@pytest.mark.parametrize(
    "capability",
    [
        AgentCapability.PLACE_LIVE_ORDER,
        AgentCapability.GRANT_OWN_TOOLS,
        AgentCapability.MUTATE_REGISTRY,
        AgentCapability.DISABLE_AUDIT,
    ],
)
def test_forbidden_capabilities_denied(capability: AgentCapability) -> None:
    result = check_agent_capability(capability)
    assert result.allowed is False
    assert "AGENT_CAPABILITY_FORBIDDEN" in result.blocker_codes


def test_unknown_capability_is_fail_closed() -> None:
    result = check_agent_capability("exfiltrate_everything")
    assert result.allowed is False
    assert "AGENT_CAPABILITY_UNKNOWN" in result.blocker_codes


def test_capability_sets_are_disjoint() -> None:
    assert ALLOWED_AGENT_CAPABILITIES.isdisjoint(FORBIDDEN_AGENT_CAPABILITIES)


# ---------------------------------------------------------------------------
# 3b. Tool-escalation gate (prompt injection)
# ---------------------------------------------------------------------------


def test_allowlisted_tool_request_passes() -> None:
    assert check_tool_request(_valid_prompt(), "canonical_read").allowed is True


def test_prompt_injection_tool_escalation_is_denied() -> None:
    """Required scenario 3: an injected request to escalate into a forbidden
    tool is denied."""
    result = check_tool_request(_valid_prompt(), "place_live_order")
    assert result.allowed is False
    assert "TOOL_FORBIDDEN" in result.blocker_codes


def test_tool_not_on_allowlist_is_denied() -> None:
    """A tool that is neither allowed nor explicitly forbidden is still denied —
    fail-closed allowlist semantics catch escalation into unlisted tools."""
    result = check_tool_request(_valid_prompt(), "exec_shell")
    assert result.allowed is False
    assert "TOOL_NOT_ALLOWLISTED" in result.blocker_codes


def test_tool_request_without_allowlist_is_fail_closed() -> None:
    prompt = PromptRegistryEntry(prompt_id="p1", allowed_tools=None)
    result = check_tool_request(prompt, "canonical_read")
    assert result.allowed is False
    assert "TOOL_ALLOWLIST_UNDEFINED" in result.blocker_codes


def test_blank_tool_request_is_denied() -> None:
    result = check_tool_request(_valid_prompt(), "   ")
    assert result.allowed is False
    assert "TOOL_REQUEST_BLANK" in result.blocker_codes


# ---------------------------------------------------------------------------
# 4. Audit registry reference validation
# ---------------------------------------------------------------------------


def test_audit_event_without_registry_reference_is_rejected() -> None:
    """Required scenario 5: an audit event with no registry reference is
    rejected."""
    result = validate_decision_audit(None)
    assert result.allowed is False
    assert "AUDIT_REGISTRY_REF_MISSING" in result.blocker_codes


def test_audit_event_with_incomplete_reference_is_rejected() -> None:
    ref = DecisionRegistryReference(
        model_id="m1",
        model_version="v1",
        prompt_id="p1",
        prompt_version="v1",
        approval_status="production_approved",
        registry_hash=None,  # <- missing
    )
    result = validate_decision_audit(ref)
    assert result.allowed is False
    assert "AUDIT_REGISTRY_HASH_MISSING" in result.blocker_codes


def test_audit_event_with_malformed_hash_is_rejected() -> None:
    ref = DecisionRegistryReference(
        model_id="m1",
        model_version="v1",
        prompt_id="p1",
        prompt_version="v1",
        approval_status="production_approved",
        registry_hash="not-a-real-sha256",
    )
    result = validate_decision_audit(ref)
    assert result.allowed is False
    assert "AUDIT_REGISTRY_HASH_MALFORMED" in result.blocker_codes


def test_complete_audit_reference_passes() -> None:
    ref = DecisionRegistryReference(
        model_id="m1",
        model_version="v1",
        prompt_id="p1",
        prompt_version="v1",
        approval_status="production_approved",
        registry_hash="a" * 64,
    )
    assert validate_decision_audit(ref).allowed is True


# ---------------------------------------------------------------------------
# Registry hash — determinism + tamper-evidence
# ---------------------------------------------------------------------------


def test_registry_hash_is_deterministic() -> None:
    h1 = compute_registry_hash(_valid_model(), _valid_prompt())
    h2 = compute_registry_hash(_valid_model(), _valid_prompt())
    assert h1 == h2
    assert len(h1) == 64


def test_registry_hash_changes_on_field_change() -> None:
    base = compute_registry_hash(_valid_model(), _valid_prompt())
    tampered_model = ModelRegistryEntry(
        model_id=_valid_model().model_id,
        version="v2",  # bumped
        eval_suite_id=_valid_model().eval_suite_id,
        approval_status=_valid_model().approval_status,
        risk_rating=_valid_model().risk_rating,
        owner=_valid_model().owner,
        last_validation_at=_valid_model().last_validation_at,
    )
    assert compute_registry_hash(tampered_model, _valid_prompt()) != base


# ---------------------------------------------------------------------------
# Combined authorization
# ---------------------------------------------------------------------------


def test_authorize_productive_decision_happy_path() -> None:
    verdict = authorize_productive_decision(
        _valid_model(),
        _valid_prompt(),
        requested_tools=["canonical_read"],
    )
    assert verdict.authorized is True
    assert verdict.registry_reference is not None
    # the produced audit reference must itself pass the audit gate
    assert validate_decision_audit(verdict.registry_reference).allowed is True


def test_authorize_blocks_and_emits_no_reference_when_model_unapproved() -> None:
    bad_model = ModelRegistryEntry(
        model_id="m1",
        version="v1",
        eval_suite_id="e1",
        approval_status=None,
        risk_rating="low",
        owner="sentr",
        last_validation_at="2026-06-05T00:00:00Z",
    )
    verdict = authorize_productive_decision(bad_model, _valid_prompt())
    assert verdict.authorized is False
    assert verdict.registry_reference is None
    assert "MODEL_APPROVAL_STATUS_MISSING" in [b.code for b in verdict.blockers]


def test_authorize_blocks_on_tool_escalation() -> None:
    verdict = authorize_productive_decision(
        _valid_model(),
        _valid_prompt(),
        requested_tools=["place_live_order"],  # forbidden
    )
    assert verdict.authorized is False
    assert verdict.registry_reference is None
    assert "TOOL_FORBIDDEN" in [b.code for b in verdict.blockers]
