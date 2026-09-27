from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

#: Der injizierte Berichtszeitpunkt aller Tests.
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)

TRANSPORT_LOG = (
    "INFO:     Started server process [811]\n"
    "TRANSPORT_VERIFIED name=litellm version=1.99.0 "
    "tree=/home/kai/transport/litellm/1.99.0-293669ce manifest=293669ce01234567\n"
    "INFO:     Uvicorn running on http://127.0.0.1:4000\n"
    "TRANSPORT_VERIFIED name=litellm version=1.99.0 "
    "tree=/home/kai/transport/litellm/1.99.0-4e65cd31 manifest=4e65cd3189abcdef\n"
)


def zeile(stunden_alt: float = 2.0, **overrides: Any) -> dict[str, Any]:
    """Eine Telemetriezeile, wie ``record_attempt_trace`` sie schreibt (v8)."""
    value: dict[str, Any] = {
        "schema_version": "v8",
        "ts": (NOW - timedelta(hours=stunden_alt)).isoformat(),
        "provider": "openai",
        "model": "gpt-4o-mini",
        "role": "primary",
        "ok": True,
        "latency_ms": 120.0,
        "error_type": None,
        "correlation_id": "corr-1",
        "call_id": "llmc_1",
        "purpose": "analysis",
        "chain_position": 0,
        "attempt": 1,
        "error_class": None,
        "outcome": "success",
        "logical_route": "standard",
        "mode": "primary",
        "transport": "litellm",
        "requested_model_alias": "kai-standard",
        "actual_provider": "openai",
        "actual_model": "gpt-4o-mini",
        "identity_proven": True,
        "retry_count": 0,
        "fallback_from": None,
        "fallback_to": None,
        "input_tokens": 100,
        "output_tokens": 20,
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "cost_usd": 0.002,
        "cost_known": True,
        "cost_source": "upstream",
        "cost_status": "OK",
        "circuit_state": "closed",
        "transport_retries": 0,
    }
    value.update(overrides)
    return value


def write_jsonl(path: Path, rows: list[object]) -> Path:
    path.write_bytes(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in rows).encode("utf-8")
    )
    return path


def write_text(path: Path, text: str) -> Path:
    path.write_bytes(text.encode("utf-8"))
    return path


def eval_report_v1(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": "litellm-shadow-eval-report/v1",
        "tool_version": "1.0.0",
        "policy_hash": "a" * 64,
        "input_sha256": "b" * 64,
        "input_files": {"1:llm_telemetry.jsonl": "c" * 64},
        "record_count": 4,
        "generated_at": (NOW - timedelta(hours=5)).isoformat(),
        "routes": ["standard"],
        "invalid_record_count": 0,
        "validation_issues": [],
        "metrics": {"standard": {"complete_pair_count": 2}},
        "decisions": {
            "standard": {
                "logical_route": "standard",
                "status": "INSUFFICIENT_EVIDENCE",
                "reasons": ["SAMPLE_COUNT_TOO_LOW"],
                "shadow_validated": False,
                "consensus_route": False,
                "primary_ready": False,
                "consensus_primary_allowed": False,
            }
        },
        "primary_ready_routes": [],
    }
    value.update(overrides)
    return value


def eval_report_v2(**overrides: Any) -> dict[str, Any]:
    beleg = {
        "proven": True,
        "referenced": True,
        "artifact": "artifacts/proofs/off_mode.json",
        "artifact_sha256": "d" * 64,
        "proven_at": "2026-09-26T08:00:00+00:00",
        "version": "04046c68",
    }
    unbelegt = {
        "proven": False,
        "referenced": False,
        "artifact": None,
        "artifact_sha256": None,
        "proven_at": None,
        "version": None,
    }
    value = eval_report_v1(
        schema_version="litellm-shadow-eval-report/v2",
        runtime_evidence={
            "off_mode_proven": beleg,
            "rollback_proven": unbelegt,
            "trading_gate_changed": False,
            "execution_gate_changed": False,
        },
    )
    value.update(overrides)
    return value


def write_json(path: Path, value: object) -> Path:
    path.write_bytes(json.dumps(value, sort_keys=True).encode("utf-8"))
    return path
