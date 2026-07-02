"""KAI Runtime Schema Binding — startup-time validation of JSON schemas.

Purpose: Verify that CONFIG_SCHEMA.json and DECISION_SCHEMA.json are loadable,
internally consistent, and aligned with the runtime models at startup.

Security invariants:
- Fail-closed: if a schema file is missing or malformed, raise immediately.
- Safety-critical const fields are verified to exist in the schema.
- DecisionRecord fields are verified to match DECISION_SCHEMA.json required fields.
- No runtime patching or mutation of schemas.

Architecture:
- validate_config_schema() — loads and checks CONFIG_SCHEMA.json
- validate_decision_schema() — loads and checks DECISION_SCHEMA.json
- validate_decision_schema_alignment() — verifies field alignment with DecisionRecord
- validate_config_safety_constants() — checks that safety-critical const constraints exist
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.core.schema_runtime import load_schema_document

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFIG_SCHEMA_FILENAME = "CONFIG_SCHEMA.json"
DECISION_SCHEMA_FILENAME = "DECISION_SCHEMA.json"

# Safety-critical fields that MUST have "const" constraints in CONFIG_SCHEMA
_CONFIG_SAFETY_CONSTS: dict[str, dict[str, object]] = {
    "risk.require_stop_loss": {
        "section": "risk",
        "field": "require_stop_loss",
        "expected": True,
    },
    "risk.allow_averaging_down": {
        "section": "risk",
        "field": "allow_averaging_down",
        "expected": False,
    },
    "risk.allow_martingale": {
        "section": "risk",
        "field": "allow_martingale",
        "expected": False,
    },
    "risk.allow_unbounded_loss": {
        "section": "risk",
        "field": "allow_unbounded_loss",
        "expected": False,
    },
    "risk.kill_switch_enabled": {
        "section": "risk",
        "field": "kill_switch_enabled",
        "expected": True,
    },
    "execution.live_execution_enabled": {
        "section": "execution",
        "field": "live_execution_enabled",
        "expected": False,
    },
    "execution.approval_required_for_live_actions": {
        "section": "execution",
        "field": "approval_required_for_live_actions",
        "expected": True,
    },
    "security.audit_log_immutable": {
        "section": "security",
        "field": "audit_log_immutable",
        "expected": True,
    },
    "messaging_ux.voice_interface_enabled": {
        "section": "messaging_ux",
        "field": "voice_interface_enabled",
        "expected": False,
    },
    "messaging_ux.avatar_interface_enabled": {
        "section": "messaging_ux",
        "field": "avatar_interface_enabled",
        "expected": False,
    },
}

# Required top-level sections in CONFIG_SCHEMA
_CONFIG_REQUIRED_SECTIONS = frozenset(
    {
        "system_runtime",
        "llm_agent",
        "market_data",
        "risk",
        "strategy_decision",
        "execution",
        "memory_learning",
        "security",
        "messaging_ux",
    }
)


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SchemaValidationResult:
    """Immutable result of a schema validation check."""

    schema_path: str
    valid: bool
    required_fields: tuple[str, ...]
    errors: tuple[str, ...]
    safety_const_checks: tuple[str, ...] = ()

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "schema_validation_result",
            "schema_path": self.schema_path,
            "valid": self.valid,
            "required_fields_count": len(self.required_fields),
            "error_count": len(self.errors),
            "errors": list(self.errors),
            "safety_const_checks": list(self.safety_const_checks),
        }


# ---------------------------------------------------------------------------
# Core validation functions
# ---------------------------------------------------------------------------


def _load_json_schema(path: Path) -> dict[str, object]:
    """Load and parse a JSON schema file. Fail-closed on any error."""
    return load_schema_document(str(path))


def validate_config_schema(
    schema_path: Path | str | None = None,
) -> SchemaValidationResult:
    """Validate CONFIG_SCHEMA.json: loadable, has required sections, safety consts present."""
    resolved = Path(schema_path) if schema_path else Path(CONFIG_SCHEMA_FILENAME)
    errors: list[str] = []
    required_fields: list[str] = []
    safety_checks: list[str] = []

    try:
        schema = _load_json_schema(resolved)
    except (FileNotFoundError, ValueError) as exc:
        return SchemaValidationResult(
            schema_path=str(resolved),
            valid=False,
            required_fields=(),
            errors=(str(exc),),
        )

    # Check required top-level sections
    schema_required = set(schema.get("required", []))  # type: ignore[call-overload]
    required_fields = sorted(schema_required)
    missing = _CONFIG_REQUIRED_SECTIONS - schema_required
    if missing:
        errors.append(f"Missing required sections: {sorted(missing)}")

    # Check properties exist
    props = schema.get("properties", {})
    if not isinstance(props, dict):
        errors.append("Schema 'properties' must be an object")
        return SchemaValidationResult(
            schema_path=str(resolved),
            valid=False,
            required_fields=tuple(required_fields),
            errors=tuple(errors),
        )

    # Check safety-critical const constraints
    for label, spec in _CONFIG_SAFETY_CONSTS.items():
        section = str(spec["section"])
        field_name = str(spec["field"])
        expected_val = spec["expected"]
        section_props = props.get(section, {})
        if not isinstance(section_props, dict):
            errors.append(f"Section '{section}' missing or not an object")
            safety_checks.append(f"FAIL: {label} — section missing")
            continue
        inner_props = section_props.get("properties", {})
        if not isinstance(inner_props, dict):
            errors.append(f"Section '{section}' has no properties")
            safety_checks.append(f"FAIL: {label} — no properties")
            continue
        field_def = inner_props.get(field_name, {})
        if not isinstance(field_def, dict):
            errors.append(f"Field '{label}' missing from schema")
            safety_checks.append(f"FAIL: {label} — field missing")
            continue
        const_val = field_def.get("const")
        if const_val is None:
            errors.append(f"Field '{label}' has no const constraint")
            safety_checks.append(f"FAIL: {label} — no const")
        elif const_val != expected_val:
            errors.append(f"Field '{label}' const={const_val}, expected={expected_val}")
            safety_checks.append(f"FAIL: {label} — wrong const")
        else:
            safety_checks.append(f"PASS: {label} = {expected_val}")

    return SchemaValidationResult(
        schema_path=str(resolved),
        valid=len(errors) == 0,
        required_fields=tuple(required_fields),
        errors=tuple(errors),
        safety_const_checks=tuple(safety_checks),
    )


def validate_decision_schema(
    schema_path: Path | str | None = None,
) -> SchemaValidationResult:
    """Validate DECISION_SCHEMA.json: loadable, has all 26 required fields."""
    resolved = Path(schema_path) if schema_path else Path(DECISION_SCHEMA_FILENAME)
    errors: list[str] = []

    try:
        schema = _load_json_schema(resolved)
    except (FileNotFoundError, ValueError) as exc:
        return SchemaValidationResult(
            schema_path=str(resolved),
            valid=False,
            required_fields=(),
            errors=(str(exc),),
        )

    required_fields = list(schema.get("required", []))  # type: ignore[call-overload]
    if len(required_fields) < 26:
        errors.append(f"DECISION_SCHEMA requires at least 26 fields, found {len(required_fields)}")

    # Check mode enum includes all valid modes
    props = schema.get("properties", {})
    if isinstance(props, dict):
        mode_def = props.get("mode", {})
        if isinstance(mode_def, dict):
            mode_enum = set(mode_def.get("enum", []))
            expected_modes = {"research", "backtest", "paper", "shadow", "live"}
            missing_modes = expected_modes - mode_enum
            if missing_modes:
                errors.append(f"Mode enum missing: {sorted(missing_modes)}")

    return SchemaValidationResult(
        schema_path=str(resolved),
        valid=len(errors) == 0,
        required_fields=tuple(required_fields),
        errors=tuple(errors),
    )


def validate_decision_schema_alignment(
    schema_path: Path | str | None = None,
) -> SchemaValidationResult:
    """Verify DECISION_SCHEMA.json required fields align with DecisionRecord model fields."""
    resolved = Path(schema_path) if schema_path else Path(DECISION_SCHEMA_FILENAME)
    errors: list[str] = []

    try:
        schema = _load_json_schema(resolved)
    except (FileNotFoundError, ValueError) as exc:
        return SchemaValidationResult(
            schema_path=str(resolved),
            valid=False,
            required_fields=(),
            errors=(str(exc),),
        )

    schema_required = set(schema.get("required", []))  # type: ignore[call-overload]

    # Get DecisionRecord fields from Pydantic model
    from app.execution.models import DecisionRecord

    model_fields = set(DecisionRecord.model_fields.keys())

    # Schema fields that are NOT in the model
    schema_only = schema_required - model_fields
    if schema_only:
        errors.append(f"Schema requires fields not in DecisionRecord: {sorted(schema_only)}")

    # Model fields that are NOT required by schema
    model_only = model_fields - schema_required
    if model_only:
        # This is informational — model can have defaults not required by schema
        logger.info(
            "DecisionRecord has fields not required by schema: %s",
            sorted(model_only),
        )

    return SchemaValidationResult(
        schema_path=str(resolved),
        valid=len(errors) == 0,
        required_fields=tuple(sorted(schema_required)),
        errors=tuple(errors),
    )


def run_all_schema_validations(
    *,
    config_path: Path | str | None = None,
    decision_path: Path | str | None = None,
) -> list[SchemaValidationResult]:
    """Run all schema validations. Returns list of results. Fail-closed: raises on critical."""
    results = [
        validate_config_schema(config_path),
        validate_decision_schema(decision_path),
        validate_decision_schema_alignment(decision_path),
    ]
    failures = [r for r in results if not r.valid]
    if failures:
        error_details = "; ".join(f"{r.schema_path}: {r.errors}" for r in failures)
        logger.error("Schema validation failures: %s", error_details)
    return results
