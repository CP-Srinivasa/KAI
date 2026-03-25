"""Canonical runtime JSON Schema validation.

This module is the runtime source of truth for payload schema binding.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

CONFIG_SCHEMA_FILENAME = "CONFIG_SCHEMA.json"
DECISION_SCHEMA_FILENAME = "DECISION_SCHEMA.json"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FORMAT_CHECKER = FormatChecker()


class SchemaValidationError(ValueError):
    """Raised when runtime payloads violate a canonical JSON schema."""


@lru_cache(maxsize=4)
def load_schema_document(schema_filename: str) -> dict[str, object]:
    """Load one bundled schema document fail-closed."""

    schema_path = _REPO_ROOT / schema_filename
    if not schema_path.exists():
        raise SchemaValidationError(f"Schema file not found: {schema_path}")
    try:
        payload = json.loads(schema_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SchemaValidationError(f"Schema file is malformed: {schema_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SchemaValidationError(f"{schema_filename} must contain a JSON object schema")
    return payload


@lru_cache(maxsize=4)
def _build_validator(schema_filename: str) -> Draft202012Validator:
    return Draft202012Validator(
        load_schema_document(schema_filename),
        format_checker=_FORMAT_CHECKER,
    )


def validate_json_schema_payload(
    payload: Mapping[str, Any],
    *,
    schema_filename: str,
    label: str,
) -> dict[str, Any]:
    """Validate untrusted payloads fail-closed against one bundled schema."""

    candidate = dict(payload)
    errors = sorted(
        _build_validator(schema_filename).iter_errors(candidate),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        first = errors[0]
        path = ".".join(str(part) for part in first.absolute_path) or "<root>"
        raise SchemaValidationError(
            f"{label} failed {schema_filename} validation at {path}: {first.message}"
        )
    return candidate


def validate_runtime_config_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Canonical runtime config validation against CONFIG_SCHEMA.json."""

    return validate_json_schema_payload(
        payload,
        schema_filename=CONFIG_SCHEMA_FILENAME,
        label="Runtime config payload",
    )


def validate_decision_schema_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Canonical runtime decision validation against DECISION_SCHEMA.json."""

    return validate_json_schema_payload(
        payload,
        schema_filename=DECISION_SCHEMA_FILENAME,
        label="Decision payload",
    )


def validate_config_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Compatibility alias for runtime config validation."""

    return validate_runtime_config_payload(payload)


def validate_decision_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Compatibility alias for runtime decision validation."""

    return validate_decision_schema_payload(payload)

