"""Redacted, effective-configuration snapshot (KAI CORE v1, §9 Observability).

The operator must be able to *prove* which configuration a running process
uses — without a single secret leaving the process. This module walks the
``AppSettings`` tree, reports every field with its effective value, replaces
anything that looks like a secret with a fingerprint marker, and records which
fields were set explicitly (env/.env) versus inherited from a code default.

Kept out of ``app/core/settings.py`` deliberately (god-file ratchet).
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from pydantic_settings import BaseSettings

from app.core.settings import AppSettings

_SECRET_NAME = re.compile(
    r"(key|token|secret|password|passphrase|macaroon|seed|salt|api_hash|api_id|credential|private)",
    re.IGNORECASE,
)
# user:password@host inside URLs (DB_URL and friends) — keep scheme/host, drop userinfo.
_URL_USERINFO = re.compile(r"^([a-z0-9+.\-]+://)([^/@]+)@", re.IGNORECASE)

REDACTED_EMPTY = "(empty)"


def fingerprint(value: str) -> str:
    """Stable 8-hex fingerprint so the operator can tell *which* secret is loaded."""
    if not value:
        return REDACTED_EMPTY
    return f"(set:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:8]})"


def _redact_value(name: str, value: Any) -> Any:
    if isinstance(value, str):
        if _SECRET_NAME.search(name):
            return fingerprint(value)
        if _URL_USERINFO.match(value):
            return _URL_USERINFO.sub(r"\1***@", value)
        return value
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_redact_value(name, item) for item in value]
    if isinstance(value, dict):
        return {str(k): _redact_value(str(k), v) for k, v in value.items()}
    return str(value)


def _walk(
    settings: BaseSettings, path: str, out: dict[str, Any], explicit: dict[str, list[str]]
) -> None:
    section: dict[str, Any] = {}
    explicit_fields = sorted(settings.model_fields_set)
    for name in settings.__class__.model_fields:
        value = getattr(settings, name)
        if isinstance(value, BaseSettings):
            _walk(value, f"{path}.{name}" if path else name, out, explicit)
            continue
        section[name] = _redact_value(name, value)
    if section:
        out[path or "app"] = section
    if explicit_fields:
        explicit[path or "app"] = [
            f for f in explicit_fields if not isinstance(getattr(settings, f, None), BaseSettings)
        ]


def redacted_config_snapshot(settings: AppSettings) -> dict[str, Any]:
    """Effective configuration, secrets replaced by fingerprints.

    Returns ``{"sections": {...}, "explicit": {...}}`` where ``explicit`` lists,
    per section, the fields that came from the environment rather than a
    default. A critical field missing from ``explicit`` is the operator's cue.
    """
    sections: dict[str, Any] = {}
    explicit: dict[str, list[str]] = {}
    _walk(settings, "", sections, explicit)
    return {"sections": sections, "explicit": {k: v for k, v in explicit.items() if v}}


def assert_no_secret_leak(snapshot: dict[str, Any], secrets: list[str]) -> None:
    """Test/CI helper: raise if any known secret value appears in the snapshot."""
    import json

    blob = json.dumps(snapshot, sort_keys=True)
    for secret in secrets:
        if secret and secret in blob:
            raise AssertionError("secret value leaked into config snapshot")
