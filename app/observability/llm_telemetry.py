"""LLM call telemetry — failure-rate + latency p50/p95 (B-002, Audit F-5).

Append-only JSONL per call (canonical writer pattern: frozen record ->
``append_lock`` -> append), read back tolerantly. Consumed by the dashboard
integrations surface and the B-002 re-entry capability check.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.file_lock import append_lock
from app.storage.jsonl_io import iter_jsonl_tolerant

DEFAULT_TELEMETRY_PATH = Path("artifacts/llm_telemetry.jsonl")


def record_llm_call(
    *,
    provider: str,
    model: str,
    ok: bool,
    latency_ms: float,
    role: str = "primary",
    error_type: str | None = None,
    logical_route: str | None = None,
    requested_model_alias: str | None = None,
    actual_provider: str | None = None,
    actual_model: str | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cached_tokens: int | None = None,
    estimated_cost: float | None = None,
    currency: str = "USD",
    retry_count: int = 0,
    fallback_count: int = 0,
    fallback_reason: str | None = None,
    fallback_from: str | None = None,
    fallback_to: str | None = None,
    attempt_number: int | None = None,
    circuit_state: str | None = None,
    request_id: str | None = None,
    schema_validation: str | None = None,
    event_scope: str = "call",
    path: Path = DEFAULT_TELEMETRY_PATH,
) -> None:
    """Append one telemetry row. Never raises into the caller (best-effort)."""
    row = {
        "schema_version": "v2",
        "ts": datetime.now(UTC).isoformat(),
        "provider": provider,
        "model": model,
        "logical_route": logical_route,
        "requested_model_alias": requested_model_alias,
        "actual_provider": actual_provider or provider,
        "actual_model": actual_model or model,
        "role": role,
        "ok": bool(ok),
        "latency_ms": round(float(latency_ms), 3),
        "prompt_tokens": max(0, int(prompt_tokens)),
        "completion_tokens": max(0, int(completion_tokens)),
        "total_tokens": max(0, int(prompt_tokens)) + max(0, int(completion_tokens)),
        "cached_tokens": max(0, int(cached_tokens)) if cached_tokens is not None else None,
        "estimated_cost": round(float(estimated_cost), 10) if estimated_cost is not None else None,
        "currency": currency,
        "retry_count": max(0, int(retry_count)),
        "fallback_count": max(0, int(fallback_count)),
        "fallback_reason": fallback_reason,
        "fallback_from": fallback_from,
        "fallback_to": fallback_to,
        "attempt_number": attempt_number,
        "circuit_state": circuit_state,
        "request_id": request_id,
        "schema_validation": schema_validation,
        "event_scope": event_scope,
        "error_type": error_type,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, sort_keys=True, separators=(",", ":"))
        with append_lock(path):
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception:  # noqa: BLE001 — telemetry must never break the analysis path
        pass


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile (p50 of [a,b,c,d] = b, p95 = d)."""
    if not sorted_values:
        return None
    import math

    idx = min(len(sorted_values) - 1, max(0, math.ceil(pct * len(sorted_values)) - 1))
    return sorted_values[idx]


def llm_telemetry_summary(
    window_hours: float = 24.0, path: Path = DEFAULT_TELEMETRY_PATH
) -> dict[str, Any]:
    """Failure-rate + latency percentiles over the window. Honest n=0 when empty."""
    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
    n = failures = fallback_count = 0
    prompt_tokens = completion_tokens = total_tokens = cached_tokens = 0
    known_cost = 0.0
    known_cost_calls = unknown_cost_calls = 0
    latencies: list[float] = []
    by_role: dict[str, int] = {}
    provider_health: dict[str, dict[str, int]] = {}
    if path.exists():
        for row in iter_jsonl_tolerant(path):
            try:
                ts = datetime.fromisoformat(str(row.get("ts", "")))
            except ValueError:
                continue
            if ts < cutoff:
                continue
            if row.get("event_scope", "call") != "call":
                continue
            n += 1
            ok = bool(row.get("ok", False))
            if not ok:
                failures += 1
            role = str(row.get("role", "primary"))
            by_role[role] = by_role.get(role, 0) + 1
            provider = str(row.get("actual_provider") or row.get("provider") or "unknown")
            health = provider_health.setdefault(provider, {"calls": 0, "failures": 0})
            health["calls"] += 1
            if not ok:
                health["failures"] += 1
            try:
                fallback_count += max(0, int(row.get("fallback_count", 0)))
            except (TypeError, ValueError):
                pass
            for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens"):
                try:
                    value = max(0, int(row.get(key, 0) or 0))
                except (TypeError, ValueError):
                    value = 0
                if key == "prompt_tokens":
                    prompt_tokens += value
                elif key == "completion_tokens":
                    completion_tokens += value
                elif key == "total_tokens":
                    total_tokens += value
                else:
                    cached_tokens += value
            raw_cost = row.get("estimated_cost")
            if raw_cost is None:
                unknown_cost_calls += 1
            else:
                try:
                    known_cost += max(0.0, float(raw_cost))
                    known_cost_calls += 1
                except (TypeError, ValueError):
                    unknown_cost_calls += 1
            try:
                latencies.append(float(row.get("latency_ms", 0.0)))
            except (TypeError, ValueError):
                continue
    latencies.sort()
    return {
        "implemented": True,  # B-002 landed 2026-07-11 (Audit F-5)
        "window_hours": window_hours,
        "n": n,
        "failures": failures,
        "failure_rate_pct": round(100.0 * failures / n, 2) if n else None,
        "latency_p50_ms": _percentile(latencies, 0.50),
        "latency_p95_ms": _percentile(latencies, 0.95),
        "fallback_count": fallback_count,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": cached_tokens,
        "estimated_cost_usd": round(known_cost, 8) if known_cost_calls else None,
        "known_cost_calls": known_cost_calls,
        "unknown_cost_calls": unknown_cost_calls,
        "by_role": dict(sorted(by_role.items())),
        "provider_health": dict(sorted(provider_health.items())),
    }
