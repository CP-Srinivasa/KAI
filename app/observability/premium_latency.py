"""Premium-Pipeline Receive→Fill latency stats + auto-eskalations-trigger (P2 #11 watch).

Why: 2026-05-14 P2-decision was "wait for trigger before building event-driven
inotify bridge". This module is the trigger-watcher itself — it sweeps the
bridge-audit log on a daily cron, computes the latency distribution, and
writes a marker file when the operator-defined threshold is crossed.

Trigger definition (rationale in kai_premium_pipeline_backlog_20260514):
- ``p95(receive_to_fill_seconds) > 20 minutes``
- AND ``sample_size >= 5`` over the last 7 days
Both clauses must hold — a single slow tick on n=2 is not a signal.

What we measure:
- ``origin_envelope_timestamp`` = approval re-emit time (envelope handed
  to bridge) — proxy for "receive into pipeline" since worker→envelope
  latency is sub-second.
- ``timestamp_utc`` of stage="filled" bridge audit record = fill time.
- ``latency_seconds = fill_ts - origin_envelope_ts``.

What we DON'T claim to measure: end-to-end (channel-post→fill). That would
require joining telegram_message_envelope.jsonl, which the trigger does
not need — the latency P2 #11 would improve is exactly the bridge-tick
gap, and that's what ``origin_envelope_timestamp`` to fill measures.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Thresholds — tunable but the defaults match the 2026-05-14 backlog spec.
DEFAULT_LOOKBACK_HOURS = 168  # 7 days
DEFAULT_TRIGGER_P95_SECONDS = 20 * 60
DEFAULT_TRIGGER_MIN_SAMPLES = 5

_BRIDGE_LOG = Path("artifacts/bridge_pending_orders.jsonl")
_BASELINE_PATH = Path("artifacts/premium_latency_audit_baseline.json")


@dataclass(frozen=True)
class LatencyStats:
    sample_size: int
    expired_count: int
    expired_pct: float
    p50_seconds: float | None
    p95_seconds: float | None
    p99_seconds: float | None
    max_seconds: float | None
    lookback_hours: int
    trigger_fired: bool
    trigger_reason: str  # "" if not fired

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    """Linear-interpolation percentile. Returns None for empty input."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * pct / 100
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_values[int(k)]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


def _get_or_init_baseline(path: Path, now: datetime) -> datetime:
    """Return the audit baseline timestamp; create it if missing.

    The baseline exists to suppress false-positive triggers from historical
    pre-fix data. Example (2026-05-14): the 12.5.-14.5. bridge-cron outage
    produced 10h+ Receive→Fill latencies that would otherwise permanently
    fire the trigger even after the fix is in place. By cutting off audit
    stats at the first-run timestamp, the trigger watches the future,
    not the past.

    Idempotent: a present file is honoured, never overwritten — that lets
    the operator pin a baseline manually if they want to reset the window.
    """
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            raw = data.get("baseline_at")
            if isinstance(raw, str):
                ts = datetime.fromisoformat(raw)
                return ts if ts.tzinfo else ts.replace(tzinfo=UTC)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "[latency] baseline read failed (%s) — re-initialising", exc
            )
    payload = {
        "baseline_at": now.isoformat(),
        "rationale": (
            "Audit baseline (P2 #11 trigger-watch). Latency samples from "
            "before this timestamp are not counted, so historical pre-fix "
            "outliers cannot fire a false-positive trigger. Operator may "
            "delete this file to reset the window; the next audit run will "
            "create a fresh baseline at that moment."
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return now


def _parse_iso(ts_raw: object) -> datetime | None:
    if not isinstance(ts_raw, str):
        return None
    try:
        ts = datetime.fromisoformat(ts_raw)
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def compute_latency_stats(
    audit_path: Path | None = None,
    *,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
    trigger_p95_seconds: int = DEFAULT_TRIGGER_P95_SECONDS,
    trigger_min_samples: int = DEFAULT_TRIGGER_MIN_SAMPLES,
    now: datetime | None = None,
    baseline_path: Path | None = None,
) -> LatencyStats:
    """Sweep the bridge-audit log, compute latency distribution, eval trigger.

    Tolerant against malformed JSON lines + missing fields. An audit log
    that doesn't exist yet returns a zero-sample LatencyStats (trigger
    can never fire at n=0).

    The effective lookback window is ``max(now - lookback_hours, baseline)``.
    On first run, ``baseline`` is initialised to ``now``, so the trigger
    starts measuring forward only — historical pre-fix outliers cannot
    fire a false-positive (see ``_get_or_init_baseline``).
    """
    path = audit_path or _BRIDGE_LOG
    current = now or datetime.now(UTC)
    lookback_cutoff = current - timedelta(hours=lookback_hours)
    baseline = _get_or_init_baseline(baseline_path or _BASELINE_PATH, current)
    cutoff = max(lookback_cutoff, baseline)

    latencies: list[float] = []
    expired_count = 0

    if path.exists():
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            logger.warning("[latency] read failed: %s", exc)
            lines = []
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _parse_iso(rec.get("timestamp_utc"))
            if ts is None or ts < cutoff:
                continue
            stage = rec.get("stage")
            if stage == "expired":
                expired_count += 1
                continue
            if stage not in ("filled", "filled_duplicate_suppressed"):
                continue
            origin_ts = _parse_iso(rec.get("origin_envelope_timestamp"))
            if origin_ts is None:
                continue
            delta = (ts - origin_ts).total_seconds()
            # Defensive: negative or absurd values are dropped (clock skew etc.)
            if 0 <= delta < 7 * 24 * 3600:
                latencies.append(delta)

    latencies.sort()
    n = len(latencies)
    p50 = _percentile(latencies, 50) if n else None
    p95 = _percentile(latencies, 95) if n else None
    p99 = _percentile(latencies, 99) if n else None
    max_lat = latencies[-1] if n else None

    total_terminal = n + expired_count
    expired_pct = (expired_count / total_terminal * 100) if total_terminal else 0.0

    trigger_fired = False
    trigger_reason = ""
    if p95 is not None and n >= trigger_min_samples and p95 > trigger_p95_seconds:
        trigger_fired = True
        trigger_reason = (
            f"p95={p95:.0f}s > {trigger_p95_seconds}s "
            f"AND samples={n} >= {trigger_min_samples}"
        )

    return LatencyStats(
        sample_size=n,
        expired_count=expired_count,
        expired_pct=round(expired_pct, 2),
        p50_seconds=round(p50, 2) if p50 is not None else None,
        p95_seconds=round(p95, 2) if p95 is not None else None,
        p99_seconds=round(p99, 2) if p99 is not None else None,
        max_seconds=round(max_lat, 2) if max_lat is not None else None,
        lookback_hours=lookback_hours,
        trigger_fired=trigger_fired,
        trigger_reason=trigger_reason,
    )


__all__ = [
    "DEFAULT_LOOKBACK_HOURS",
    "DEFAULT_TRIGGER_MIN_SAMPLES",
    "DEFAULT_TRIGGER_P95_SECONDS",
    "LatencyStats",
    "compute_latency_stats",
]
