"""Streaming JSONL loader and fail-closed KAI telemetry normalizer."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.litellm_shadow_eval.models import EvidenceRecord, Side, ValidationIssue

SUPPORTED_SCHEMA_VERSIONS = frozenset({"v1", "v2", "litellm-shadow-eval/v1"})
_SECRET_MARKERS = ("api_key", "authorization", "secret", "token", "password", "audio")


@dataclass(frozen=True, slots=True)
class LoadedEvidence:
    records: tuple[EvidenceRecord, ...]
    issues: tuple[ValidationIssue, ...]
    input_files: dict[str, str]
    input_sha256: str
    record_count: int


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _schema_valid(raw: dict[str, Any]) -> bool | None:
    explicit = _bool(raw.get("schema_valid"))
    if explicit is not None:
        return explicit
    status = _text(raw.get("schema_status"))
    if status is None or status.upper() == "UNKNOWN":
        return None
    if status.lower() in {"valid", "ok", "pass", "passed"}:
        return True
    if status.lower() in {"invalid", "error", "fail", "failed"}:
        return False
    return None


def _side(raw: dict[str, Any]) -> Side | None:
    explicit = _text(raw.get("side"))
    if explicit:
        try:
            return Side(explicit.upper())
        except ValueError:
            return None
    role = (_text(raw.get("role")) or "").lower()
    transport = (_text(raw.get("transport")) or "").lower()
    if role == "shadow":
        return Side.SHADOW
    if transport == "direct" or role == "direct":
        return Side.DIRECT
    if transport == "litellm" and role == "primary":
        return Side.SHADOW
    return None


def _fallback(raw: dict[str, Any]) -> bool | None:
    explicit = _bool(raw.get("fallback_used"))
    if explicit is not None:
        return explicit
    if _text(raw.get("fallback_from")) or _text(raw.get("fallback_to")):
        return True
    return None


def _canonical_line(raw_line: str, parsed: object | None) -> bytes:
    if parsed is None:
        return raw_line.strip().encode("utf-8")
    return json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _issue(
    issues: list[ValidationIssue],
    code: str,
    message: str,
    record_ref: str,
    route: str | None,
) -> None:
    issues.append(ValidationIssue(code, message, record_ref, route))


def normalize_record(
    raw: dict[str, Any], *, record_ref: str
) -> tuple[EvidenceRecord | None, tuple[ValidationIssue, ...]]:
    """Normalize only documented fields; prompts, headers and secrets are ignored."""
    issues: list[ValidationIssue] = []
    schema_version = _text(raw.get("schema_version"))
    route = _text(raw.get("logical_route")) or _text(raw.get("route"))
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        _issue(issues, "UNKNOWN_SCHEMA_VERSION", "unsupported or missing schema", record_ref, route)
    if route is None:
        _issue(issues, "MISSING_ROUTE", "logical_route is required", record_ref, None)
    purpose = _text(raw.get("purpose"))
    if purpose is None:
        _issue(issues, "MISSING_PURPOSE", "purpose is required", record_ref, route)
    side = _side(raw)
    if side is None:
        _issue(issues, "INVALID_SIDE", "side must be DIRECT or SHADOW", record_ref, route)

    success = _bool(raw.get("success"))
    if success is None:
        success = _bool(raw.get("ok"))
    if success is None:
        _issue(issues, "MISSING_SUCCESS", "success/ok must be boolean", record_ref, route)

    timestamp = _text(raw.get("timestamp")) or _text(raw.get("ts"))
    if timestamp is None:
        _issue(issues, "MISSING_TIMESTAMP", "timestamp/ts is required", record_ref, route)

    evaluation_id = _text(raw.get("evaluation_id"))
    correlation_id = _text(raw.get("correlation_id"))
    call_id = _text(raw.get("call_id"))
    if evaluation_id is None and not (correlation_id and call_id and route):
        _issue(
            issues,
            "UNPAIRABLE_RECORD",
            "evaluation_id or correlation_id+call_id+route is required",
            record_ref,
            route,
        )

    latency = _number(raw.get("latency_ms"))
    input_tokens = _integer(raw.get("input_tokens"))
    if input_tokens is None:
        input_tokens = _integer(raw.get("prompt_tokens"))
    output_tokens = _integer(raw.get("output_tokens"))
    if output_tokens is None:
        output_tokens = _integer(raw.get("completion_tokens"))
    cost = _number(raw.get("cost_usd"))
    quality = _number(raw.get("quality_score"))

    for code, value, label in (
        ("NEGATIVE_LATENCY", latency, "latency_ms"),
        ("NEGATIVE_INPUT_TOKENS", input_tokens, "input_tokens"),
        ("NEGATIVE_OUTPUT_TOKENS", output_tokens, "output_tokens"),
        ("NEGATIVE_COST", cost, "cost_usd"),
    ):
        if value is not None and value < 0:
            _issue(issues, code, f"{label} must be non-negative", record_ref, route)
    if quality is not None and not 0.0 <= quality <= 1.0:
        _issue(issues, "INVALID_QUALITY_SCORE", "quality_score must be in [0,1]", record_ref, route)

    cost_known = _bool(raw.get("cost_known"))
    if cost_known is None:
        cost_known = cost is not None
    if cost_known and cost is None:
        _issue(issues, "KNOWN_COST_MISSING", "cost_known=true requires cost_usd", record_ref, route)
    if not cost_known and cost is not None:
        _issue(
            issues,
            "UNKNOWN_COST_HAS_VALUE",
            "cost_known=false forbids a fabricated numeric cost",
            record_ref,
            route,
        )

    actual_provider = _text(raw.get("actual_provider"))
    actual_model = _text(raw.get("actual_model"))
    identity_proven = _bool(raw.get("identity_proven")) or False
    if identity_proven and (actual_provider is None or actual_model is None):
        _issue(
            issues,
            "IDENTITY_PROOF_INCOMPLETE",
            "identity_proven=true requires actual_provider and actual_model",
            record_ref,
            route,
        )

    execution_authority = _bool(raw.get("execution_authority"))
    if side is Side.SHADOW and execution_authority is True:
        _issue(
            issues,
            "SHADOW_EXECUTION_AUTHORITY",
            "SHADOW may never have execution authority",
            record_ref,
            route,
        )
    mode = _text(raw.get("mode"))
    primary_explicit = (
        _bool(raw.get("primary_evidence")) is True
        or (_text(raw.get("record_kind")) or "").upper() == "PRIMARY"
    )
    if (mode or "").lower() == "primary" and not primary_explicit:
        _issue(
            issues,
            "AMBIGUOUS_PRIMARY_RECORD",
            "mode=primary requires primary_evidence=true or record_kind=PRIMARY",
            record_ref,
            route,
        )

    retry_count = _integer(raw.get("retry_count"))
    physical_attempt = _integer(raw.get("attempt"))
    attempt_count = _integer(raw.get("attempt_count"))
    if retry_count is None and physical_attempt is not None and physical_attempt >= 1:
        retry_count = physical_attempt - 1
    if attempt_count is None and physical_attempt is not None:
        attempt_count = physical_attempt
    for code, value, label in (
        ("NEGATIVE_RETRY_COUNT", retry_count, "retry_count"),
        ("INVALID_ATTEMPT_COUNT", attempt_count, "attempt_count"),
    ):
        lower = 0 if label == "retry_count" else 1
        if value is not None and value < lower:
            _issue(issues, code, f"{label} is out of range", record_ref, route)

    if issues:
        return None, tuple(issues)
    assert schema_version is not None and route is not None and purpose is not None
    assert side is not None and success is not None and timestamp is not None
    return (
        EvidenceRecord(
            schema_version=schema_version,
            evaluation_id=evaluation_id,
            correlation_id=correlation_id,
            call_id=call_id,
            logical_route=route,
            purpose=purpose,
            side=side,
            mode=mode,
            requested_alias=_text(raw.get("requested_alias"))
            or _text(raw.get("requested_model_alias")),
            actual_provider=actual_provider,
            actual_model=actual_model,
            identity_proven=identity_proven,
            success=success,
            schema_valid=_schema_valid(raw),
            outcome=_text(raw.get("outcome")),
            error_class=_text(raw.get("error_class")),
            fallback_used=_fallback(raw),
            retry_count=retry_count,
            attempt_count=attempt_count,
            latency_ms=latency,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            cost_known=cost_known,
            response_fingerprint=_text(raw.get("response_fingerprint")),
            quality_score=quality,
            timestamp=timestamp,
            execution_authority=execution_authority,
            record_ref=record_ref,
        ),
        (),
    )


def load_evidence(paths: list[Path]) -> LoadedEvidence:
    """Read JSONL incrementally and retain only compact normalized records."""
    records: list[EvidenceRecord] = []
    issues: list[ValidationIssue] = []
    file_hashes: dict[str, str] = {}
    record_count = 0

    for input_index, path in enumerate(sorted(paths, key=lambda item: str(item)), start=1):
        # Keep only fixed-size digests, never raw payloads/prompts. This makes
        # provenance order-independent without retaining large JSONL lines.
        semantic_line_hashes: list[bytes] = []
        # Absolute workstation paths can contain operator/user identity. The
        # report needs a stable input label and hash, not that personal path.
        display_path = f"{input_index}:{path.name}"
        try:
            handle = path.open(encoding="utf-8")
        except OSError as exc:
            issues.append(
                ValidationIssue("INPUT_UNREADABLE", type(exc).__name__, display_path, None)
            )
            file_hashes[display_path] = hashlib.sha256(b"").hexdigest()
            continue
        with handle:
            for line_number, raw_line in enumerate(handle, start=1):
                stripped = raw_line.strip()
                if not stripped:
                    continue
                record_count += 1
                record_ref = f"{display_path}:{line_number}"
                try:
                    parsed: object | None = json.loads(stripped)
                except json.JSONDecodeError:
                    parsed = None
                    issues.append(
                        ValidationIssue("MALFORMED_JSONL", "line is not valid JSON", record_ref)
                    )
                semantic_line_hashes.append(
                    hashlib.sha256(_canonical_line(stripped, parsed)).digest()
                )
                if not isinstance(parsed, dict):
                    if parsed is not None:
                        issues.append(
                            ValidationIssue(
                                "INVALID_RECORD", "JSONL record must be an object", record_ref
                            )
                        )
                    continue
                record, row_issues = normalize_record(parsed, record_ref=record_ref)
                issues.extend(row_issues)
                if record is not None:
                    records.append(record)
        digest = hashlib.sha256()
        for semantic_hash in sorted(semantic_line_hashes):
            digest.update(semantic_hash)
            digest.update(b"\n")
        file_hashes[display_path] = digest.hexdigest()

    aggregate = hashlib.sha256()
    for file_digest in sorted(file_hashes.values()):
        aggregate.update(file_digest.encode("ascii"))
        aggregate.update(b"\n")
    return LoadedEvidence(
        records=tuple(records),
        issues=tuple(issues),
        input_files=file_hashes,
        input_sha256=aggregate.hexdigest(),
        record_count=record_count,
    )


def contains_secret_field(payload: dict[str, Any]) -> bool:
    """Test helper documenting fields that must never be copied to reports."""
    return any(marker in key.lower() for key in payload for marker in _SECRET_MARKERS)


__all__ = [
    "LoadedEvidence",
    "SUPPORTED_SCHEMA_VERSIONS",
    "contains_secret_field",
    "load_evidence",
    "normalize_record",
]
