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
from uuid import uuid4

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
    path: Path = DEFAULT_TELEMETRY_PATH,
    # --- v2 (NEO-P-001, 2026-09-02) -------------------------------------
    # Purely additive keyword-only fields. Every v1 call site stays valid and
    # keeps writing the same v1 keys at the same place, so the dashboard
    # reader (llm_telemetry_summary) is unaffected. No new artifact stream.
    correlation_id: str | None = None,
    call_id: str | None = None,
    purpose: str | None = None,
    chain_position: int = 0,
    attempt: int = 1,
    error_class: str | None = None,
    http_status: int | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    outcome: str | None = None,
) -> None:
    """Append one telemetry row. Never raises into the caller (best-effort)."""
    row: dict[str, Any] = {
        "schema_version": "v2",
        "ts": datetime.now(UTC).isoformat(),
        "provider": provider,
        "model": model,
        "role": role,
        "ok": bool(ok),
        "latency_ms": round(float(latency_ms), 3),
        "error_type": error_type,
        "correlation_id": correlation_id,
        "call_id": call_id or f"llmc_{uuid4().hex[:8]}",
        "purpose": purpose,
        "chain_position": int(chain_position),
        "attempt": int(attempt),
        "error_class": error_class,
        "http_status": http_status,
        "prompt_tokens": int(prompt_tokens),
        "completion_tokens": int(completion_tokens),
        "outcome": outcome or ("success" if ok else "exhausted"),
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
    n = failures = 0
    latencies: list[float] = []
    if path.exists():
        for row in iter_jsonl_tolerant(path):
            try:
                ts = datetime.fromisoformat(str(row.get("ts", "")))
            except ValueError:
                continue
            if ts < cutoff:
                continue
            n += 1
            if not row.get("ok", False):
                failures += 1
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
    }
