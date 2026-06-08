"""Governance registry persistence (Issue #165).

Pins the persistence + loader behaviour that wires the gates into production:
- round-trip model/prompt entries through JSONL
- lookup by (id, version); unknown → None (fail-closed at the gate)
- missing file → empty mapping; malformed row skipped, never crashes
- decision governance audit sidecar append/load
"""

from __future__ import annotations

from pathlib import Path

from app.security.governance.models import ModelRegistryEntry, PromptRegistryEntry
from app.security.governance.registry_store import (
    DecisionRegistryReference,
    append_decision_governance_audit,
    load_decision_governance_audit,
    load_model_registry,
    load_prompt_registry,
    lookup_model,
    lookup_prompt,
    save_model_registry_entry,
    save_prompt_registry_entry,
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


def test_model_registry_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "model_registry.jsonl"
    save_model_registry_entry(_model(), path)
    reg = load_model_registry(path)
    entry = lookup_model(reg, "internal-rule-heuristic", "v1")
    assert entry is not None
    assert entry.approval_status == "production_approved"
    assert entry.owner == "sentr"


def test_prompt_registry_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "prompt_registry.jsonl"
    save_prompt_registry_entry(_prompt(), path)
    reg = load_prompt_registry(path)
    entry = lookup_prompt(reg, "signal-analyst", "v3")
    assert entry is not None
    assert entry.allowed_tools == ("canonical_read",)
    assert entry.forbidden_tools == ("place_live_order",)


def test_lookup_unknown_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "model_registry.jsonl"
    save_model_registry_entry(_model(), path)
    reg = load_model_registry(path)
    assert lookup_model(reg, "ghost", "v9") is None


def test_missing_file_yields_empty_registry(tmp_path: Path) -> None:
    assert load_model_registry(tmp_path / "nope.jsonl") == {}
    assert load_prompt_registry(tmp_path / "nope.jsonl") == {}


def test_malformed_row_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "model_registry.jsonl"
    save_model_registry_entry(_model(), path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{ this is not json\n")
        fh.write("\n")  # blank line tolerated
    reg = load_model_registry(path)
    assert lookup_model(reg, "internal-rule-heuristic", "v1") is not None
    assert len(reg) == 1


def test_last_write_wins_per_key(tmp_path: Path) -> None:
    path = tmp_path / "model_registry.jsonl"
    save_model_registry_entry(_model(), path)
    # re-approve same (id, version) with a different owner
    save_model_registry_entry(
        ModelRegistryEntry(
            model_id="internal-rule-heuristic",
            version="v1",
            eval_suite_id="eval-2026-07",
            approval_status="production_approved",
            risk_rating="low",
            owner="operator",
            last_validation_at="2026-06-08T00:00:00Z",
        ),
        path,
    )
    reg = load_model_registry(path)
    assert lookup_model(reg, "internal-rule-heuristic", "v1").owner == "operator"


def test_entry_without_key_fields_is_not_indexed(tmp_path: Path) -> None:
    path = tmp_path / "model_registry.jsonl"
    # missing version → unkeyable → never resolves a decision
    save_model_registry_entry(ModelRegistryEntry(model_id="x", version=None), path)
    assert load_model_registry(path) == {}


def test_decision_governance_audit_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "decision_governance_audit.jsonl"
    ref = DecisionRegistryReference(
        model_id="m",
        model_version="v1",
        prompt_id="p",
        prompt_version="v3",
        approval_status="approved",
        registry_hash="a" * 64,
    )
    append_decision_governance_audit(
        decision_id="dec_1",
        reference=ref,
        authorized=True,
        blocker_codes=[],
        timestamp_utc="2026-06-08T00:00:00Z",
        path=path,
    )
    rows = load_decision_governance_audit(path)
    assert len(rows) == 1
    assert rows[0]["decision_id"] == "dec_1"
    assert rows[0]["authorized"] is True
    assert rows[0]["registry_reference"]["registry_hash"] == "a" * 64
