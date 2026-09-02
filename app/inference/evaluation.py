"""Honest summary of safe shadow comparison artifacts."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from app.storage.jsonl_io import iter_jsonl_tolerant

DEFAULT_SHADOW_COMPARISON_PATH = Path("artifacts/inference_shadow.jsonl")


def inspect_shadow_comparison_stream(
    path: Path = DEFAULT_SHADOW_COMPARISON_PATH,
) -> list[str]:
    """Return contract violations for an existing stream; absence is healthy/off."""
    if not path.exists():
        return []
    problems: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [f"unreadable:{type(exc).__name__}"]
    import json

    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            problems.append(f"line_{number}:invalid_json")
            continue
        if not isinstance(row, dict):
            problems.append(f"line_{number}:not_object")
            continue
        if row.get("authoritative") != "current" or row.get("influences_execution") is not False:
            problems.append(f"line_{number}:authority_contract_invalid")
    return problems


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


def shadow_evaluation_summary(path: Path = DEFAULT_SHADOW_COMPARISON_PATH) -> dict[str, Any]:
    analysis_n = schema_passes = direction_disagreements = critical_disagreements = 0
    consensus_n = consensus_disagreements = 0
    candidate_tokens = known_cost_calls = unknown_cost_calls = 0
    known_cost = 0.0
    candidate_latencies: list[float] = []
    if path.exists():
        for row in iter_jsonl_tolerant(path):
            divergence = row.get("divergence")
            if not isinstance(divergence, dict):
                continue
            if row.get("kind") == "signal_consensus":
                consensus_n += 1
                consensus_disagreements += int(
                    bool(divergence.get("agreement_disagreement", False))
                )
                continue
            current = row.get("current")
            candidate = row.get("candidate")
            if not isinstance(current, dict) or not isinstance(candidate, dict):
                continue
            analysis_n += 1
            schema_pass = bool(candidate.get("schema_pass", False))
            schema_passes += int(schema_pass)
            if schema_pass:
                direction_disagreements += int(
                    bool(divergence.get("direction_disagreement", False))
                )
                critical_disagreements += int(
                    bool(divergence.get("critical_field_disagreement", False))
                )
            for key in ("prompt_tokens", "completion_tokens"):
                try:
                    candidate_tokens += max(0, int(candidate.get(key, 0) or 0))
                except (TypeError, ValueError):
                    pass
            try:
                latency = candidate.get("latency_ms")
                if latency is not None:
                    candidate_latencies.append(max(0.0, float(latency)))
            except (TypeError, ValueError):
                pass
            cost = candidate.get("estimated_cost_usd")
            if cost is None:
                unknown_cost_calls += 1
            else:
                try:
                    known_cost += max(0.0, float(cost))
                    known_cost_calls += 1
                except (TypeError, ValueError):
                    unknown_cost_calls += 1

    return {
        "analysis_comparisons": analysis_n,
        "validated_analysis_comparisons": schema_passes,
        "candidate_schema_success_rate_pct": (
            round(100.0 * schema_passes / analysis_n, 3) if analysis_n else None
        ),
        "direction_disagreement_rate_pct": (
            round(100.0 * direction_disagreements / schema_passes, 3) if schema_passes else None
        ),
        "critical_field_disagreement_rate_pct": (
            round(100.0 * critical_disagreements / schema_passes, 3) if schema_passes else None
        ),
        "candidate_latency_p50_ms": _percentile(candidate_latencies, 0.50),
        "candidate_latency_p95_ms": _percentile(candidate_latencies, 0.95),
        "candidate_tokens": candidate_tokens,
        "candidate_known_cost_usd": round(known_cost, 8) if known_cost_calls else None,
        "candidate_cost_per_1000_calls_usd": (
            round(known_cost / known_cost_calls * 1000.0, 8) if known_cost_calls else None
        ),
        "candidate_unknown_cost_calls": unknown_cost_calls,
        "consensus_comparisons": consensus_n,
        "consensus_disagreement_rate_pct": (
            round(100.0 * consensus_disagreements / consensus_n, 3) if consensus_n else None
        ),
        "primary_recommendation": "NOT_PROVEN",
    }
