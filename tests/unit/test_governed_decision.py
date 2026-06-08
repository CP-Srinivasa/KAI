"""Governed decision append — fail-closed wiring (Issue #165).

Pins the productive-path contract:
- an authorized decision is appended AND its registry reference persisted
- an unauthorized decision (unknown/unapproved model or prompt) is refused
  fail-closed: NO journal record, a refusal audit record, GovernanceRejectedError
- validate_decision_audit is a hard gate before the append
- resolve_and_append resolves entries from the persisted registries
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.orchestrator.decision_journal import (
    RiskAssessment,
    create_decision_instance,
    load_decision_journal,
)
from app.orchestrator.governed_decision import (
    GovernanceRejectedError,
    authorize_and_append_decision,
    resolve_and_append_decision,
)
from app.security.governance.models import ModelRegistryEntry, PromptRegistryEntry
from app.security.governance.registry_store import (
    load_decision_governance_audit,
    save_model_registry_entry,
    save_prompt_registry_entry,
)


def _decision():
    return create_decision_instance(
        symbol="BTC/USDT",
        market="crypto",
        venue="paper",
        mode="paper",
        thesis="test thesis long enough to pass",
        supporting_factors=["a"],
        confidence_score=0.6,
        market_regime="bull",
        volatility_state="low",
        liquidity_state="high",
        risk_assessment=RiskAssessment(
            risk_level="low", max_position_pct=2.0, drawdown_remaining_pct=10.0
        ),
        entry_logic="entry now on breakout",
        exit_logic="exit at target or stop",
        stop_loss=50000.0,
        invalidation_condition="price below the stop line",
        position_size_rationale="small fixed size",
        max_loss_estimate=10.0,
        data_sources_used=["src"],
        model_version="v1",
        prompt_version="v3",
    )


def _model() -> ModelRegistryEntry:
    return ModelRegistryEntry(
        model_id="internal-rule-heuristic",
        version="v1",
        eval_suite_id="eval-2026-06",
        approval_status="production_approved",
        risk_rating="low",
        owner="sentr",
        last_validation_at="2026-06-05T00:00:00Z",
    )


def _prompt() -> PromptRegistryEntry:
    return PromptRegistryEntry(
        prompt_id="signal-analyst",
        prompt_version="v3",
        owner_agent="neo",
        allowed_tools=("canonical_read",),
        forbidden_tools=("place_live_order",),
        output_contract="LLMAnalysisOutput",
        prompt_injection_eval_status="passed",
        approval_status="approved",
    )


def test_authorized_decision_is_appended_and_referenced(tmp_path: Path) -> None:
    journal = tmp_path / "decision_journal.jsonl"
    sidecar = tmp_path / "governance_audit.jsonl"
    decision = _decision()

    verdict = authorize_and_append_decision(
        decision,
        journal,
        model_entry=_model(),
        prompt_entry=_prompt(),
        governance_audit_path=sidecar,
    )

    assert verdict.authorized is True
    assert verdict.registry_reference is not None
    assert verdict.registry_reference.registry_hash
    # journal record written
    records = load_decision_journal(journal)
    assert len(records) == 1
    # reference persisted alongside (keyed by decision_id)
    audit = load_decision_governance_audit(sidecar)
    assert len(audit) == 1
    assert audit[0]["authorized"] is True
    assert audit[0]["decision_id"] == str(decision.decision_id)
    assert (
        audit[0]["registry_reference"]["registry_hash"] == verdict.registry_reference.registry_hash
    )


def test_unknown_model_is_refused_fail_closed(tmp_path: Path) -> None:
    journal = tmp_path / "decision_journal.jsonl"
    sidecar = tmp_path / "governance_audit.jsonl"

    with pytest.raises(GovernanceRejectedError):
        authorize_and_append_decision(
            _decision(),
            journal,
            model_entry=None,  # unknown model → fail-closed
            prompt_entry=_prompt(),
            governance_audit_path=sidecar,
        )

    # NO journal record written
    assert load_decision_journal(journal) == []
    # a refusal audit record IS written for auditability
    audit = load_decision_governance_audit(sidecar)
    assert len(audit) == 1
    assert audit[0]["authorized"] is False
    assert "MODEL_ENTRY_MISSING" in audit[0]["blocker_codes"]


def test_unapproved_model_is_refused(tmp_path: Path) -> None:
    journal = tmp_path / "decision_journal.jsonl"
    sidecar = tmp_path / "governance_audit.jsonl"
    unapproved = ModelRegistryEntry(
        model_id="x",
        version="v1",
        eval_suite_id="e",
        approval_status="experimental",  # not productive
        risk_rating="low",
        owner="o",
        last_validation_at="2026-06-05T00:00:00Z",
    )

    with pytest.raises(GovernanceRejectedError):
        authorize_and_append_decision(
            _decision(),
            journal,
            model_entry=unapproved,
            prompt_entry=_prompt(),
            governance_audit_path=sidecar,
        )
    assert load_decision_journal(journal) == []


def test_resolve_and_append_from_registries(tmp_path: Path) -> None:
    journal = tmp_path / "decision_journal.jsonl"
    sidecar = tmp_path / "governance_audit.jsonl"
    model_reg = tmp_path / "model_registry.jsonl"
    prompt_reg = tmp_path / "prompt_registry.jsonl"
    save_model_registry_entry(_model(), model_reg)
    save_prompt_registry_entry(_prompt(), prompt_reg)

    import app.orchestrator.governed_decision as gd

    # point the resolver at the temp registries
    orig_model = gd.load_model_registry
    orig_prompt = gd.load_prompt_registry
    gd.load_model_registry = lambda *a, **k: orig_model(model_reg)  # type: ignore[assignment]
    gd.load_prompt_registry = lambda *a, **k: orig_prompt(prompt_reg)  # type: ignore[assignment]
    try:
        verdict = resolve_and_append_decision(
            _decision(),
            journal,
            model_id="internal-rule-heuristic",
            model_version="v1",
            prompt_id="signal-analyst",
            prompt_version="v3",
            governance_audit_path=sidecar,
        )
    finally:
        gd.load_model_registry = orig_model  # type: ignore[assignment]
        gd.load_prompt_registry = orig_prompt  # type: ignore[assignment]

    assert verdict.authorized is True
    assert len(load_decision_journal(journal)) == 1


def test_resolve_unknown_identity_refused(tmp_path: Path) -> None:
    journal = tmp_path / "decision_journal.jsonl"
    sidecar = tmp_path / "governance_audit.jsonl"
    model_reg = tmp_path / "model_registry.jsonl"
    prompt_reg = tmp_path / "prompt_registry.jsonl"
    save_model_registry_entry(_model(), model_reg)
    save_prompt_registry_entry(_prompt(), prompt_reg)

    import app.orchestrator.governed_decision as gd

    orig_model = gd.load_model_registry
    orig_prompt = gd.load_prompt_registry
    gd.load_model_registry = lambda *a, **k: orig_model(model_reg)  # type: ignore[assignment]
    gd.load_prompt_registry = lambda *a, **k: orig_prompt(prompt_reg)  # type: ignore[assignment]
    try:
        with pytest.raises(GovernanceRejectedError):
            resolve_and_append_decision(
                _decision(),
                journal,
                model_id="ghost-model",  # not in registry
                model_version="v9",
                prompt_id="signal-analyst",
                prompt_version="v3",
                governance_audit_path=sidecar,
            )
    finally:
        gd.load_model_registry = orig_model  # type: ignore[assignment]
        gd.load_prompt_registry = orig_prompt  # type: ignore[assignment]

    assert load_decision_journal(journal) == []
