"""Sprint 37: Runtime Schema Binding & Decision Backbone Convergence.

Tests that DECISION_SCHEMA.json and CONFIG_SCHEMA.json are real runtime
contracts — validated at write-time, fail-closed on violations.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.core.schema_runtime import (
    SchemaValidationError,
    validate_config_payload,
    validate_decision_payload,
)
from app.core.settings import (
    AppSettings,
    build_runtime_config_payload,
)
from app.core.settings import (
    validate_runtime_config_payload as settings_validate_runtime_config_payload,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid_decision_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "decision_id": "dec_abc1234567890",
        "timestamp_utc": "2026-03-21T10:00:00+00:00",
        "symbol": "BTC/USDT",
        "market": "crypto",
        "venue": "paper_binance",
        "mode": "paper",
        "thesis": "BTC breaking out on ETF inflow confirmed by strong volume.",
        "supporting_factors": ["volume spike", "RSI momentum"],
        "contradictory_factors": ["macro risk"],
        "confidence_score": 0.75,
        "market_regime": "bullish",
        "volatility_state": "moderate",
        "liquidity_state": "healthy",
        "risk_assessment": {"summary": "Low risk trade.", "risk_level": "low"},
        "entry_logic": {"summary": "Buy on breakout", "conditions": ["close above 68k"]},
        "exit_logic": {"summary": "Sell on invalidation", "conditions": ["close below 65k"]},
        "stop_loss": 65000.0,
        "take_profit": 72000.0,
        "invalidation_condition": "Daily close below 65k",
        "position_size_rationale": "0.25% risk per trade",
        "max_loss_estimate": 100.0,
        "data_sources_used": ["on_chain_data", "tradingview"],
        "model_version": "kai-paper-v1",
        "prompt_version": "decision-pack-v1",
        "approval_state": "audit_only",
        "execution_state": "not_executable",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# SchemaValidationError type
# ---------------------------------------------------------------------------


def test_schema_validation_error_is_value_error() -> None:
    assert issubclass(SchemaValidationError, ValueError)


def test_schema_validation_error_can_be_raised_and_caught() -> None:
    with pytest.raises(ValueError):
        raise SchemaValidationError("test")


# ---------------------------------------------------------------------------
# validate_decision_payload — valid inputs
# ---------------------------------------------------------------------------


def test_valid_decision_payload_passes() -> None:
    validate_decision_payload(_valid_decision_payload())


def test_valid_decision_payload_null_take_profit_passes() -> None:
    validate_decision_payload(_valid_decision_payload(take_profit=None))


def test_valid_decision_payload_null_stop_loss_passes() -> None:
    validate_decision_payload(_valid_decision_payload(stop_loss=None))


def test_valid_decision_payload_empty_contradictory_passes() -> None:
    validate_decision_payload(_valid_decision_payload(contradictory_factors=[]))


def test_valid_decision_payload_all_approval_states() -> None:
    for state in ("pending", "approved", "rejected", "not_required", "audit_only"):
        validate_decision_payload(_valid_decision_payload(approval_state=state))


def test_valid_decision_payload_all_execution_states() -> None:
    for state in (
        "not_executable",
        "queued",
        "paper_only",
        "shadow_only",
        "ready",
        "blocked",
        "executed",
        "failed",
    ):
        validate_decision_payload(_valid_decision_payload(execution_state=state))


def test_valid_decision_payload_all_modes() -> None:
    for mode in ("research", "backtest", "paper", "shadow", "live"):
        validate_decision_payload(_valid_decision_payload(mode=mode))


# ---------------------------------------------------------------------------
# validate_decision_payload — invalid inputs (fail-closed)
# ---------------------------------------------------------------------------


def test_invalid_approval_state_raises_schema_error() -> None:
    with pytest.raises(SchemaValidationError):
        validate_decision_payload(_valid_decision_payload(approval_state="auto_approved_paper"))


def test_invalid_execution_state_raises_schema_error() -> None:
    with pytest.raises(SchemaValidationError):
        validate_decision_payload(_valid_decision_payload(execution_state="submitted"))


def test_invalid_mode_raises_schema_error() -> None:
    with pytest.raises(SchemaValidationError):
        validate_decision_payload(_valid_decision_payload(mode="live_high_frequency"))


def test_missing_required_field_raises_schema_error() -> None:
    payload = _valid_decision_payload()
    del payload["thesis"]
    with pytest.raises(SchemaValidationError):
        validate_decision_payload(payload)


def test_wrong_type_confidence_score_raises_schema_error() -> None:
    with pytest.raises(SchemaValidationError):
        validate_decision_payload(_valid_decision_payload(confidence_score="not-a-number"))


def test_confidence_score_out_of_range_raises_schema_error() -> None:
    with pytest.raises(SchemaValidationError):
        validate_decision_payload(_valid_decision_payload(confidence_score=1.5))


def test_missing_risk_assessment_summary_raises_schema_error() -> None:
    payload = _valid_decision_payload(
        risk_assessment={"risk_level": "low"}  # missing required "summary"
    )
    with pytest.raises(SchemaValidationError):
        validate_decision_payload(payload)


def test_additional_property_raises_schema_error() -> None:
    payload = _valid_decision_payload()
    payload["unknown_field_xyz"] = "should fail"
    with pytest.raises(SchemaValidationError):
        validate_decision_payload(payload)


# ---------------------------------------------------------------------------
# validate_config_payload — fail-closed
# ---------------------------------------------------------------------------


def test_empty_config_payload_raises_schema_error() -> None:
    with pytest.raises(SchemaValidationError):
        validate_config_payload({})


def test_missing_config_section_raises_schema_error() -> None:
    with pytest.raises(SchemaValidationError):
        validate_config_payload({"system_runtime": {}})


def test_settings_wrapper_matches_canonical_runtime_validator() -> None:
    payload = build_runtime_config_payload(AppSettings(_env_file=None))

    canonical = validate_config_payload(payload)
    via_settings = settings_validate_runtime_config_payload(payload)

    assert canonical == via_settings


# ---------------------------------------------------------------------------
# Schema files are present and readable
# ---------------------------------------------------------------------------


def test_decision_schema_file_exists() -> None:
    assert Path("DECISION_SCHEMA.json").exists()


def test_config_schema_file_exists() -> None:
    assert Path("CONFIG_SCHEMA.json").exists()


# ---------------------------------------------------------------------------
# Old enum values rejected at schema layer (Sprint 37 invariant)
# ---------------------------------------------------------------------------


def test_legacy_auto_approved_paper_not_in_schema() -> None:
    """auto_approved_paper was removed in Sprint 37 — schema rejects it."""
    with pytest.raises(SchemaValidationError):
        validate_decision_payload(_valid_decision_payload(approval_state="auto_approved_paper"))


def test_legacy_submitted_not_in_schema() -> None:
    """submitted was removed in Sprint 37 — schema rejects it."""
    with pytest.raises(SchemaValidationError):
        validate_decision_payload(_valid_decision_payload(execution_state="submitted"))


def test_legacy_filled_not_in_schema() -> None:
    """filled was removed in Sprint 37 — schema rejects it."""
    with pytest.raises(SchemaValidationError):
        validate_decision_payload(_valid_decision_payload(execution_state="filled"))


def test_legacy_partial_not_in_schema() -> None:
    """partial was removed in Sprint 37 — schema rejects it."""
    with pytest.raises(SchemaValidationError):
        validate_decision_payload(_valid_decision_payload(execution_state="partial"))
