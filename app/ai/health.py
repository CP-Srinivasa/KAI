"""Provider health derived from the telemetry stream — no probe calls.

NEO-F-009: ``/health`` reported liveness and the dashboard reported *configuration*
("active" because a key is set). Neither says whether a provider actually works.

This snapshot is computed purely from ``artifacts/llm_telemetry.jsonl``. That is
a deliberate constraint, not a shortcut: a probe call would cost money, add a
failure domain to a health endpoint, and still only prove that one synthetic
request worked. Real traffic is the better evidence.

No-Fake-Doktrin: ``state`` is ``unknown`` at n=0 — never ``ok``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.observability.llm_telemetry import (
    DEFAULT_TELEMETRY_PATH,
    _percentile,  # identical nearest-rank definition; re-implementing it would drift
)
from app.storage.jsonl_io import iter_jsonl_tolerant

CHAIN_SOURCE = "app/analysis/factory.py"

_DEGRADED_AT_PCT = 10.0
_DOWN_AT_PCT = 50.0
_DOWN_AT_CONSECUTIVE_FAILURES = 3


def _row_ts(row: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(str(row.get("ts", "")))
    except ValueError:
        return None


def _chain_position(row: dict[str, Any]) -> int:
    try:
        return int(row.get("chain_position", -1))
    except (TypeError, ValueError):
        return -1


def _load_rows(path: Path, window_hours: float) -> list[dict[str, Any]]:
    """In-window rows, oldest first, with outer wrapper rows de-duplicated.

    An ``chain_position == -1`` row spans a whole fallback chain. When the same
    correlation id also produced per-attempt rows, counting both would double
    every ensemble call, so the wrapper is dropped. v1 rows (no correlation id)
    are always kept — they have no per-attempt counterpart.
    """
    if not path.exists():
        return []
    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
    rows: list[dict[str, Any]] = []
    for row in iter_jsonl_tolerant(path):
        if not isinstance(row, dict):
            continue
        ts = _row_ts(row)
        if ts is None or ts < cutoff:
            continue
        rows.append(row)
    rows.sort(key=lambda r: str(r.get("ts", "")))

    attempt_cids = {
        row.get("correlation_id")
        for row in rows
        if _chain_position(row) >= 0 and row.get("correlation_id")
    }
    return [
        row
        for row in rows
        if not (_chain_position(row) == -1 and row.get("correlation_id") in attempt_cids)
    ]


def _classify_state(calls: int, failures: int, consecutive_failures: int) -> str:
    if calls == 0:
        return "unknown"
    if consecutive_failures >= _DOWN_AT_CONSECUTIVE_FAILURES:
        return "down"
    rate = 100.0 * failures / calls
    if rate > _DOWN_AT_PCT:
        return "down"
    if rate >= _DEGRADED_AT_PCT:
        return "degraded"
    return "ok"


def _provider_block(name: str, rows: list[dict[str, Any]], *, configured: bool) -> dict[str, Any]:
    calls = len(rows)
    failures = sum(1 for row in rows if not row.get("ok", False))

    latencies: list[float] = []
    for row in rows:
        try:
            latencies.append(float(row.get("latency_ms", 0.0)))
        except (TypeError, ValueError):
            continue
    latencies.sort()

    last_ok_ts: str | None = None
    last_error_class: str | None = None
    for row in rows:
        if row.get("ok", False):
            last_ok_ts = str(row.get("ts")) if row.get("ts") else last_ok_ts
        else:
            error_class = row.get("error_class")
            last_error_class = str(error_class) if error_class else last_error_class

    consecutive_failures = 0
    for row in reversed(rows):
        if row.get("ok", False):
            break
        consecutive_failures += 1

    return {
        "name": name,
        "configured": configured,
        "state": _classify_state(calls, failures, consecutive_failures),
        "calls": calls,
        "failures": failures,
        "failure_rate_pct": round(100.0 * failures / calls, 2) if calls else None,
        "latency_p50_ms": _percentile(latencies, 0.50),
        "latency_p95_ms": _percentile(latencies, 0.95),
        "last_ok_ts": last_ok_ts,
        "last_error_class": last_error_class,
        "consecutive_failures": consecutive_failures,
    }


def ai_health_snapshot(
    window_hours: float = 24.0,
    path: Path | None = None,
    settings: Any | None = None,
) -> dict[str, Any]:
    """Per-provider health over *window_hours*, plus the configured chain.

    Args:
        window_hours: look-back window over the telemetry stream.
        path: telemetry sink; ``None`` uses the default at call time.
        settings: AppSettings; ``None`` loads them. Only read, never written.

    Returns:
        ``{"ai": {"chain": ..., "window_hours": ..., "providers": [...]}}``
    """
    from app.analysis.factory import describe_primary_chain, describe_shadow_chain

    if settings is None:
        from app.core.settings import get_settings

        settings = get_settings()

    primary = describe_primary_chain(settings)
    shadow = describe_shadow_chain(settings)
    configured = set(primary) | set(shadow)

    sink = path if path is not None else DEFAULT_TELEMETRY_PATH
    rows = _load_rows(sink, window_hours)

    by_provider: dict[str, list[dict[str, Any]]] = {name: [] for name in primary + shadow}
    for row in rows:
        provider = row.get("provider")
        if not isinstance(provider, str) or not provider:
            continue
        by_provider.setdefault(provider, []).append(row)

    # Chain order first (stable, reviewable), then anything else seen in traffic.
    ordered = primary + [name for name in shadow if name not in primary]
    ordered += sorted(name for name in by_provider if name not in ordered)

    return {
        "ai": {
            "chain": {"primary": primary, "shadow": shadow, "source": CHAIN_SOURCE},
            "window_hours": window_hours,
            "providers": [
                _provider_block(name, by_provider[name], configured=name in configured)
                for name in ordered
            ],
        }
    }
