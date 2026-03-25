"""Compatibility shim for runtime schema validation.

Canonical implementation now lives in `app.core.schema_runtime`.
"""

from __future__ import annotations

from app.core.schema_runtime import (
    CONFIG_SCHEMA_FILENAME,
    DECISION_SCHEMA_FILENAME,
    SchemaValidationError,
    load_schema_document,
    validate_config_payload,
    validate_decision_payload,
    validate_decision_schema_payload,
    validate_json_schema_payload,
    validate_runtime_config_payload,
)

__all__ = [
    "CONFIG_SCHEMA_FILENAME",
    "DECISION_SCHEMA_FILENAME",
    "SchemaValidationError",
    "load_schema_document",
    "validate_json_schema_payload",
    "validate_runtime_config_payload",
    "validate_decision_schema_payload",
    "validate_config_payload",
    "validate_decision_payload",
]

