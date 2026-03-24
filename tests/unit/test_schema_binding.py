"""Tests for app/core/schema_binding.py — Runtime Schema Binding.

Covers:
- CONFIG_SCHEMA.json loading and validation
- DECISION_SCHEMA.json loading and validation
- Safety-critical const constraint verification
- DecisionRecord field alignment with schema
- Fail-closed behavior (missing/malformed files)
- SchemaValidationResult immutability
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.schema_binding import (
    SchemaValidationResult,
    run_all_schema_validations,
    validate_config_schema,
    validate_decision_schema,
    validate_decision_schema_alignment,
)

# ---------------------------------------------------------------------------
# Real schema validation (against actual repo files)
# ---------------------------------------------------------------------------


def test_config_schema_loads_and_validates() -> None:
    result = validate_config_schema(Path("CONFIG_SCHEMA.json"))
    assert result.valid, f"CONFIG_SCHEMA errors: {result.errors}"
    assert len(result.required_fields) == 9


def test_decision_schema_loads_and_validates() -> None:
    result = validate_decision_schema(Path("DECISION_SCHEMA.json"))
    assert result.valid, f"DECISION_SCHEMA errors: {result.errors}"
    assert len(result.required_fields) >= 26


def test_decision_schema_alignment_with_model() -> None:
    result = validate_decision_schema_alignment(Path("DECISION_SCHEMA.json"))
    assert result.valid, f"Alignment errors: {result.errors}"


def test_config_safety_consts_all_pass() -> None:
    """All 10 safety-critical const constraints must be present and correct."""
    result = validate_config_schema(Path("CONFIG_SCHEMA.json"))
    assert result.valid
    pass_count = sum(1 for c in result.safety_const_checks if c.startswith("PASS"))
    assert pass_count == 10, f"Expected 10 PASS, got {pass_count}: {result.safety_const_checks}"


def test_run_all_validations() -> None:
    results = run_all_schema_validations(
        config_path=Path("CONFIG_SCHEMA.json"),
        decision_path=Path("DECISION_SCHEMA.json"),
    )
    assert len(results) == 3
    assert all(r.valid for r in results), f"Failures: {[r.errors for r in results if not r.valid]}"


# ---------------------------------------------------------------------------
# Fail-closed behavior
# ---------------------------------------------------------------------------


def test_config_schema_missing_file(tmp_path: Path) -> None:
    result = validate_config_schema(tmp_path / "nonexistent.json")
    assert result.valid is False
    assert any("not found" in e for e in result.errors)


def test_decision_schema_missing_file(tmp_path: Path) -> None:
    result = validate_decision_schema(tmp_path / "nonexistent.json")
    assert result.valid is False
    assert any("not found" in e for e in result.errors)


def test_config_schema_malformed_json(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{invalid json", encoding="utf-8")
    result = validate_config_schema(bad)
    assert result.valid is False
    assert any("malformed" in e.lower() for e in result.errors)


def test_decision_schema_malformed_json(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json at all", encoding="utf-8")
    result = validate_decision_schema(bad)
    assert result.valid is False


# ---------------------------------------------------------------------------
# Missing safety consts
# ---------------------------------------------------------------------------


def test_config_schema_missing_safety_const(tmp_path: Path) -> None:
    """Config schema without const on risk.require_stop_loss must fail."""
    minimal = {
        "required": [
            "system_runtime",
            "llm_agent",
            "market_data",
            "risk",
            "strategy_decision",
            "execution",
            "memory_learning",
            "security",
            "messaging_ux",
        ],
        "properties": {
            "system_runtime": {"properties": {}},
            "llm_agent": {"properties": {}},
            "market_data": {"properties": {}},
            "risk": {
                "properties": {
                    "require_stop_loss": {"type": "boolean"},  # no const!
                    "allow_averaging_down": {"type": "boolean", "const": False},
                    "allow_martingale": {"type": "boolean", "const": False},
                    "allow_unbounded_loss": {"type": "boolean", "const": False},
                    "kill_switch_enabled": {"type": "boolean", "const": True},
                }
            },
            "strategy_decision": {"properties": {}},
            "execution": {
                "properties": {
                    "live_execution_enabled": {"type": "boolean", "const": False},
                    "approval_required_for_live_actions": {"type": "boolean", "const": True},
                }
            },
            "memory_learning": {"properties": {}},
            "security": {
                "properties": {
                    "audit_log_immutable": {"type": "boolean", "const": True},
                }
            },
            "messaging_ux": {
                "properties": {
                    "voice_interface_enabled": {"type": "boolean", "const": False},
                    "avatar_interface_enabled": {"type": "boolean", "const": False},
                }
            },
        },
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(minimal), encoding="utf-8")
    result = validate_config_schema(path)
    assert result.valid is False
    assert any("require_stop_loss" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Decision schema with too few required fields
# ---------------------------------------------------------------------------


def test_decision_schema_too_few_required(tmp_path: Path) -> None:
    minimal = {
        "required": ["decision_id", "symbol"],
        "properties": {
            "decision_id": {"type": "string"},
            "symbol": {"type": "string"},
        },
    }
    path = tmp_path / "decision.json"
    path.write_text(json.dumps(minimal), encoding="utf-8")
    result = validate_decision_schema(path)
    assert result.valid is False
    assert any("26" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Decision schema missing mode enum
# ---------------------------------------------------------------------------


def test_decision_schema_missing_mode_values(tmp_path: Path) -> None:
    # 26 required fields but mode enum is incomplete
    required = [
        "decision_id",
        "timestamp_utc",
        "symbol",
        "market",
        "venue",
        "mode",
        "thesis",
        "supporting_factors",
        "contradictory_factors",
        "confidence_score",
        "market_regime",
        "volatility_state",
        "liquidity_state",
        "risk_assessment",
        "entry_logic",
        "exit_logic",
        "stop_loss",
        "take_profit",
        "invalidation_condition",
        "position_size_rationale",
        "max_loss_estimate",
        "data_sources_used",
        "model_version",
        "prompt_version",
        "approval_state",
        "execution_state",
    ]
    schema = {
        "required": required,
        "properties": {
            "mode": {"type": "string", "enum": ["paper", "research"]},  # missing 3
        },
    }
    path = tmp_path / "dec.json"
    path.write_text(json.dumps(schema), encoding="utf-8")
    result = validate_decision_schema(path)
    assert result.valid is False
    assert any("mode" in e.lower() or "missing" in e.lower() for e in result.errors)


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


def test_schema_validation_result_frozen() -> None:
    r = SchemaValidationResult(
        schema_path="test",
        valid=True,
        required_fields=(),
        errors=(),
    )
    with pytest.raises(AttributeError):
        r.valid = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# to_json_dict completeness
# ---------------------------------------------------------------------------


def test_result_to_json_dict() -> None:
    r = SchemaValidationResult(
        schema_path="x.json",
        valid=True,
        required_fields=("a", "b"),
        errors=(),
        safety_const_checks=("PASS: test",),
    )
    d = r.to_json_dict()
    assert d["valid"] is True
    assert d["required_fields_count"] == 2
    assert d["error_count"] == 0
