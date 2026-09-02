"""Safe normalized A/B comparison for operational inference shadow calls."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from app.analysis.base.interfaces import LLMAnalysisOutput
from app.core.file_lock import append_lock


def _normalized(output: LLMAnalysisOutput) -> dict[str, object]:
    return {
        "schema_pass": True,
        "direction": output.sentiment_label.value,
        "sentiment": output.sentiment_score,
        "confidence": output.confidence_score,
        "directional_confidence": output.directional_confidence,
        "priority": output.recommended_priority,
        "risk": output.spam_probability,
        "actionable": output.actionable,
        "provider": output.provider_used,
        "model": output.model_used,
        "logical_route": output.logical_route,
        "prompt_tokens": output.prompt_tokens,
        "completion_tokens": output.completion_tokens,
        "latency_ms": output.latency_ms,
        # Unknown direct-provider pricing stays null; it is never represented as zero.
        "estimated_cost_usd": output.estimated_cost_usd,
    }


def record_analysis_shadow_comparison(
    *,
    title: str,
    text: str,
    current: LLMAnalysisOutput,
    candidate: LLMAnalysisOutput,
    path: Path,
) -> None:
    """Append safe comparison features; never persist prompts or document text."""
    source_hash = hashlib.sha256(f"{title}\0{text}".encode()).hexdigest()
    current_norm = _normalized(current)
    candidate_norm = _normalized(candidate)
    row = {
        "schema_version": "v1",
        "ts": datetime.now(UTC).isoformat(),
        "source_hash": source_hash,
        "current": current_norm,
        "candidate": candidate_norm,
        "divergence": {
            "direction_disagreement": (
                current.sentiment_label.value != candidate.sentiment_label.value
            ),
            "critical_field_disagreement": (
                current.actionable != candidate.actionable
                or current.recommended_priority != candidate.recommended_priority
            ),
            "confidence_abs_delta": round(
                abs(current.confidence_score - candidate.confidence_score), 6
            ),
            "sentiment_abs_delta": round(
                abs(current.sentiment_score - candidate.sentiment_score), 6
            ),
            "priority_abs_delta": abs(
                current.recommended_priority - candidate.recommended_priority
            ),
        },
        "authoritative": "current",
        "influences_execution": False,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, sort_keys=True, separators=(",", ":"))
        with append_lock(path):
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception:  # noqa: BLE001 -- shadow audit cannot change authoritative result
        return


def record_analysis_shadow_failure(
    *,
    title: str,
    text: str,
    current: LLMAnalysisOutput,
    error_type: str,
    path: Path,
) -> None:
    """Record a failed candidate without persisting exception text or input."""
    row = {
        "schema_version": "v1",
        "ts": datetime.now(UTC).isoformat(),
        "source_hash": hashlib.sha256(f"{title}\0{text}".encode()).hexdigest(),
        "current": _normalized(current),
        "candidate": {
            "schema_pass": False,
            "error_type": error_type,
            "estimated_cost_usd": None,
            "latency_ms": None,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        },
        "divergence": {
            "direction_disagreement": None,
            "critical_field_disagreement": None,
        },
        "authoritative": "current",
        "influences_execution": False,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, sort_keys=True, separators=(",", ":"))
        with append_lock(path):
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception:  # noqa: BLE001 -- shadow audit cannot change authoritative result
        return


def record_consensus_shadow_comparison(
    *,
    symbol: str,
    direction: str,
    current_agreed: bool,
    current_confidence: float,
    candidate_agreed: bool,
    candidate_confidence: float,
    candidate_provider: str | None,
    candidate_model: str | None,
    path: Path,
) -> None:
    """Audit consensus divergence without exposing thesis or market context."""
    row = {
        "schema_version": "v1",
        "ts": datetime.now(UTC).isoformat(),
        "kind": "signal_consensus",
        "symbol": symbol,
        "direction": direction,
        "current": {
            "agreed": current_agreed,
            "confidence": current_confidence,
        },
        "candidate": {
            "agreed": candidate_agreed,
            "confidence": candidate_confidence,
            "provider": candidate_provider,
            "model": candidate_model,
        },
        "divergence": {
            "agreement_disagreement": current_agreed != candidate_agreed,
            "confidence_abs_delta": round(abs(current_confidence - candidate_confidence), 6),
        },
        "authoritative": "current",
        "influences_execution": False,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, sort_keys=True, separators=(",", ":"))
        with append_lock(path):
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception:  # noqa: BLE001 -- shadow audit cannot change consensus
        return
