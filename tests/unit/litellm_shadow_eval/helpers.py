from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.litellm_shadow_eval.models import RuntimeEvidenceFlags


def row(side: str, number: int = 0, **overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": "litellm-shadow-eval/v1",
        "evaluation_id": f"eval-{number}",
        "correlation_id": f"corr-{number}",
        "call_id": f"call-{number}",
        "logical_route": "standard",
        "purpose": "analysis",
        "side": side,
        "mode": "off" if side == "DIRECT" else "shadow",
        "requested_alias": "kai-standard",
        "actual_provider": "openai",
        "actual_model": "gpt-4o-mini",
        "identity_proven": True,
        "success": True,
        "schema_valid": True,
        "outcome": "success",
        "error_class": None,
        "fallback_used": False,
        "retry_count": 0,
        "attempt_count": 1,
        "latency_ms": 10.0 if side == "DIRECT" else 12.0,
        "input_tokens": 10,
        "output_tokens": 5,
        "cost_usd": 0.01 if side == "DIRECT" else 0.008,
        "cost_known": True,
        "response_fingerprint": f"fingerprint-{number}",
        "timestamp": "2026-09-04T00:00:00+00:00",
        "execution_authority": side == "DIRECT",
    }
    value.update(overrides)
    return value


def write_jsonl(path: Path, rows: list[object]) -> Path:
    path.write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in rows), encoding="utf-8"
    )
    return path


def proven_flags(**overrides: bool) -> RuntimeEvidenceFlags:
    values = {
        "off_mode_proven": True,
        "rollback_proven": True,
        "gateway_down_proven": True,
        "timeout_retry_proven": True,
        "rate_limit_retry_proven": True,
        "auth_no_retry_proven": True,
        "server_error_retry_proven": True,
        "circuit_proven": True,
        "direct_fallback_proven": True,
        "trading_gate_changed": False,
        "execution_gate_changed": False,
    }
    values.update(overrides)
    return RuntimeEvidenceFlags(**values)
